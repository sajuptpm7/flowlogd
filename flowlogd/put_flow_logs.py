import sys
import os
from jcsclient import client
#import put_logs as PS
import create_cross_account_policies as CP
from datetime import datetime , timedelta
import ConfigParser
import json
import getopt
import urllib2
import requests
import pdb
import datetime
import pytz
import write_to_file as WF
import constants
import utils
from vpccrypto import secret as vpcsecret
### Usage info
LOG = utils.get_logger()

# create bucket
def create_bucket(bucket):
    LOG.info(jclient.dss.create_bucket(['create-bucket','--bucket', bucket]))

#put objects into the bucket
def put_logs(directory,bucket,f):
    LOG.info('put object %s into bucket %s' % (f,bucket))
    LOG.info( jclient.dss.put_object(['put-object','--bucket', bucket
                                              ,'--key', f
                                              ,'--body', directory+'/'+f]))


def initiate_client(secret):
    LOG.info('initializing jcsclient')
    ##### Change this stuff and make it dynamic
    jclient = client.Client(access_key = vpcsecret.decrypt(secret['access_key']), secret_key = vpcsecret.decrypt(secret['secret_key']),
                            vpc_url=secret['vpc_url'],
                            dss_url=secret['dss_url'],
                            iam_url=secret['iam_url'] )

    return jclient

#global declaration of config file
CONFIG = ConfigParser.ConfigParser()
CONFIG.read(constants.CONFIG_FILENAME)
secret = WF.config_section_map(CONFIG, 'secret')
logs = WF.config_section_map(CONFIG, 'logs')
flowlog_purge_days = int(CONFIG.get('task', 'flowlog_purge_days', 7))
jclient = initiate_client(secret)

#creating bucket and cross account policy 

def policy_update(config,bucket_name,dss_account_id):
    ''' Give full path of the config file.
        It should have time delt in days.
        Environment
        bucket_name
        policy_name
        policy_action
        resources for that policy
        accounts which should have those policy attached
    '''

    LOG.info('creating bucket %s' % bucket_name)
    create_bucket(bucket_name)


    account_id = dss_account_id
    resources= []
    for dict1 in config['resources']:
        dict1['account_id']= account_id
        dict1['resource']= 'Bucket:'+bucket_name
        resources.append(dict1)
  
    #creating cross account policy
    CP.create_resource_based_policy(bucket_name,[], [], jclient)
    CP.update_resource_based_policy(bucket_name,config['accounts'], config['actions'], jclient)
    CP.attach_policy_to_resource(bucket_name,resources,jclient)

def write_to_dss(account_id,directory,b_dir,file_name):
    bucket = WF.config_section_map(CONFIG, 'bucket')
    bucket['actions'] = bucket['actions'].split(',')
    bucket['accounts'] = account_id[20:]
    bucket['resources'] = [json.loads(resource) for resource in bucket['resources'].split(',')]
    bucket_name=b_dir
    res = jclient.dss.head_bucket(['head-bucket','--bucket',bucket_name])
    
    if not os.path.exists(directory) or res['status'] != 200:
    	policy_update(bucket,bucket_name,vpcsecret.decrypt(logs['dss_account_id']))
    put_logs(directory,bucket_name,file_name)
    

def get_logs(account_id, bucket_name, start_time=None):
    time_interval = logs['time_interval']
    
    if start_time is None:
        end_time= datetime.datetime.now()
    	start_time= end_time - datetime.timedelta(seconds = int(time_interval))
    else:
        start_time= datetime.datetime.strptime(start_time, constants.DATETIME_FORMAT)
        end_time= start_time + datetime.timedelta(seconds = int(time_interval))
    base_directory = bucket_name
    directory = '/tmp/'+ base_directory
    file_name= base_directory+'-'+start_time.strftime('%d_%m_%Y-%H_%M')
    start_time= start_time.strftime(constants.DATETIME_FORMAT)
    end_time= end_time.strftime(constants.DATETIME_FORMAT)

    LOG.info('account id: %s start_time: %s end_time: %s' % (account_id,start_time,end_time))
    if not os.path.exists(directory):
        os.makedirs(directory)
        LOG.info('creating directory %s' % directory)
    #below code will create the file and append the output such that json property will not be destroyed

    with open(directory+'/'+file_name, 'a') as outfile:
        outfile.write('{ "log_data" : [')
    if WF.get_log_in_time(start_time,end_time,directory,file_name,account_id,0,'destvn'):
        with open(directory+'/'+file_name, 'a') as outfile:
            outfile.write(',')
    WF.get_log_in_time(start_time,end_time,directory,file_name,account_id,1,'sourcevn')
    with open(directory+'/'+file_name, 'a') as outfile:
        outfile.write(']}')
    
    # create bucket and cross account policy
    write_to_dss(account_id,directory,base_directory,file_name)
    os.remove(directory+'/'+file_name)
    LOG.info('Successfully written logs for account_id: %s start_time: %s and end_time: %s' % (account_id,start_time,end_time))
    return end_time

def get_log_enable_account_ids():

    res = jclient.vpc.describe_flow_log_enable_accounts('describe-flow-log-enable-accounts')
    return res['DescribeFlowLogEnableAccountsResponse']['accountIds']['item']

def delete_flows_objects(account_data):
    bucket_name = account_data['bucketName']
    account_id = account_data['projectId']
    LOG.info('Deleting logs older than {days} Days, from bucket:{bucket_name}, account:{account_id}'
             ''.format(days=flowlog_purge_days, bucket_name=bucket_name, account_id=account_id))
    count=0
    obs = jclient.dss.list_objects(['list-objects','--bucket',bucket_name])
    if obs['status'] != 200 :
        LOG.info('Bucket not created yet')
        return
    if 'Contents' in obs['ListBucketResult']:
        obs = obs['ListBucketResult']['Contents']
        if isinstance(obs, dict):
            obs = [obs['ListBucketResult']['Contents']]
    	for ob in obs:
            if ob is None:
                LOG.info('No objects found for bucket %s' % bucket_name)
                return
            cdate = datetime.datetime.strptime(ob['Key'][-16:],"%d_%m_%Y-%H_%M")
            if cdate <= datetime.datetime.now() - datetime.timedelta(days=flowlog_purge_days):
                jclient.dss.delete_object(['delete-object','--bucket',bucket_name,'--key',ob['Key']])
                count=count+1
    LOG.info('Deleted {count} logs from bucket:{bucket_name}, account:{account_id}'
             ''.format(count=count, bucket_name=bucket_name, account_id=account_id))
