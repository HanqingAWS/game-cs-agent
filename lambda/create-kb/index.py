"""
Custom Resource Lambda: Create Bedrock Knowledge Base with AOSS vector store.
Handles Create/Update/Delete lifecycle events from CloudFormation.
"""
import boto3
import json
import time
import urllib3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

# Send CloudFormation response manually (no cfnresponse dependency needed)
http = urllib3.PoolManager()


def send_cfn_response(event, context, status, data=None, physical_id=None):
    body = json.dumps({
        'Status': status,
        'Reason': f'See CloudWatch Log Stream: {context.log_stream_name}',
        'PhysicalResourceId': physical_id or context.log_stream_name,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'NoEcho': False,
        'Data': data or {},
    })
    http.request('PUT', event['ResponseURL'], body=body,
                 headers={'Content-Type': '', 'Content-Length': str(len(body))})


def ensure_aoss_access(collection_name, bedrock_role_arn, lambda_role_arn):
    """Update AOSS data access policy to include Lambda role"""
    aoss = boto3.client('opensearchserverless')
    policy_name = 'game-cs-kb-access'
    new_policy = json.dumps([{
        'Rules': [
            {'Resource': [f'collection/{collection_name}'], 'Permission': ['aoss:*'], 'ResourceType': 'collection'},
            {'Resource': [f'index/{collection_name}/*'], 'Permission': ['aoss:*'], 'ResourceType': 'index'},
        ],
        'Principal': [bedrock_role_arn, lambda_role_arn],
    }])
    try:
        current = aoss.get_access_policy(name=policy_name, type='data')
        version = current['accessPolicyDetail']['policyVersion']
        aoss.update_access_policy(name=policy_name, type='data', policy=new_policy, policyVersion=version)
        print(f'Updated access policy with principals: {bedrock_role_arn}, {lambda_role_arn}')
    except Exception as e:
        print(f'Policy update failed ({e}), trying create...')
        aoss.create_access_policy(name=policy_name, type='data', policy=new_policy)
        print('Created access policy')


def create_vector_index(endpoint, region):
    """Create HNSW vector index in AOSS collection"""
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    url = f'{endpoint}/game-cs-index'
    body = json.dumps({
        'settings': {'index': {'knn': True, 'knn.algo_param.ef_search': 512}},
        'mappings': {
            'properties': {
                'vector': {
                    'type': 'knn_vector', 'dimension': 1024,
                    'method': {'engine': 'faiss', 'name': 'hnsw', 'parameters': {}},
                },
                'text': {'type': 'text'},
                'metadata': {'type': 'text'},
                'AMAZON_BEDROCK_TEXT_CHUNK': {'type': 'text'},
                'AMAZON_BEDROCK_METADATA': {'type': 'text'},
            }
        }
    })
    req = AWSRequest(method='PUT', url=url, data=body,
                     headers={'Content-Type': 'application/json'})
    SigV4Auth(creds, 'aoss', region).add_auth(req)
    resp = urllib3.PoolManager().request('PUT', url, body=body, headers=dict(req.headers))
    print(f'Create index response: {resp.status} {resp.data.decode()[:500]}')
    if resp.status not in (200, 201):
        raise Exception(f'Index creation failed: {resp.status}')


def handler(event, context):
    print(f'Event type: {event["RequestType"]}')
    props = event['ResourceProperties']

    try:
        if event['RequestType'] == 'Create':
            region = props['Region']
            collection_arn = props['CollectionArn']
            collection_endpoint = props['CollectionEndpoint']
            bedrock_role_arn = props['RoleArn']
            lambda_role_arn = props['LambdaRoleArn']
            collection_name = 'game-cs-kb'

            # Step 1: Ensure AOSS access policy includes this Lambda's role
            print('Ensuring AOSS access policy...')
            ensure_aoss_access(collection_name, bedrock_role_arn, lambda_role_arn)

            # Step 2: Wait for access policy propagation
            print('Waiting 90s for AOSS access policy propagation...')
            time.sleep(90)

            # Step 3: Create vector index with retries
            print('Creating vector index...')
            for attempt in range(5):
                try:
                    create_vector_index(collection_endpoint, region)
                    print('Vector index created successfully')
                    break
                except Exception as e:
                    print(f'Attempt {attempt+1}/5 failed: {e}')
                    if attempt < 4:
                        time.sleep(30)
                    else:
                        raise

            time.sleep(10)

            # Step 4: Create Knowledge Base
            print('Creating Knowledge Base...')
            bedrock = boto3.client('bedrock-agent')
            kb = bedrock.create_knowledge_base(
                name=props['KnowledgeBaseName'],
                description='Game Customer Service FAQ Knowledge Base',
                roleArn=bedrock_role_arn,
                knowledgeBaseConfiguration={
                    'type': 'VECTOR',
                    'vectorKnowledgeBaseConfiguration': {
                        'embeddingModelArn': f'arn:aws:bedrock:{region}::foundation-model/cohere.embed-multilingual-v3',
                    },
                },
                storageConfiguration={
                    'type': 'OPENSEARCH_SERVERLESS',
                    'opensearchServerlessConfiguration': {
                        'collectionArn': collection_arn,
                        'vectorIndexName': 'game-cs-index',
                        'fieldMapping': {
                            'vectorField': 'vector',
                            'textField': 'AMAZON_BEDROCK_TEXT_CHUNK',
                            'metadataField': 'AMAZON_BEDROCK_METADATA',
                        },
                    },
                },
            )
            kb_id = kb['knowledgeBase']['knowledgeBaseId']
            print(f'Knowledge Base created: {kb_id}')

            # Step 5: Create S3 Data Source
            ds = bedrock.create_data_source(
                knowledgeBaseId=kb_id,
                name=f'{props["KnowledgeBaseName"]}-s3-source',
                dataSourceConfiguration={
                    'type': 'S3',
                    's3Configuration': {
                        'bucketArn': props['BucketArn'],
                        'inclusionPrefixes': ['documents/'],
                    },
                },
            )
            ds_id = ds['dataSource']['dataSourceId']
            print(f'Data Source created: {ds_id}')

            # Step 6: Start ingestion and wait
            bedrock.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
            for i in range(18):
                time.sleep(10)
                jobs = bedrock.list_ingestion_jobs(knowledgeBaseId=kb_id, dataSourceId=ds_id)
                status = jobs['ingestionJobSummaries'][0]['status']
                print(f'Ingestion status: {status}')
                if status in ('COMPLETE', 'FAILED'):
                    break

            send_cfn_response(event, context, 'SUCCESS',
                              {'KnowledgeBaseId': kb_id, 'DataSourceId': ds_id}, kb_id)

        elif event['RequestType'] == 'Delete':
            kb_id = event.get('PhysicalResourceId', '')
            if kb_id and not kb_id.startswith('2026'):  # skip log stream IDs
                try:
                    bedrock = boto3.client('bedrock-agent')
                    for ds in bedrock.list_data_sources(knowledgeBaseId=kb_id).get('dataSourceSummaries', []):
                        bedrock.delete_data_source(knowledgeBaseId=kb_id, dataSourceId=ds['dataSourceId'])
                    bedrock.delete_knowledge_base(knowledgeBaseId=kb_id)
                    print(f'Deleted KB: {kb_id}')
                except Exception as e:
                    print(f'Delete KB error (ignored): {e}')
            send_cfn_response(event, context, 'SUCCESS', {}, kb_id)

        else:  # Update
            send_cfn_response(event, context, 'SUCCESS',
                              {'KnowledgeBaseId': event.get('PhysicalResourceId', '')},
                              event.get('PhysicalResourceId', ''))

    except Exception as e:
        print(f'ERROR: {e}')
        send_cfn_response(event, context, 'FAILED', {'Error': str(e)})
