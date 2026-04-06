"""
Custom Resource Lambda: Create Bedrock Knowledge Base with AOSS vector store.
Used as onEventHandler for CDK cr.Provider - must RETURN dict, not send HTTP response.
"""
import boto3
import json
import time


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
        try:
            aoss.update_access_policy(name=policy_name, type='data', policy=new_policy, policyVersion=version)
            print(f'Updated access policy with principals: {bedrock_role_arn}, {lambda_role_arn}')
        except aoss.exceptions.ValidationException:
            print('Access policy already has correct content, no update needed')
    except aoss.exceptions.ResourceNotFoundException:
        try:
            aoss.create_access_policy(name=policy_name, type='data', policy=new_policy)
            print('Created access policy')
        except aoss.exceptions.ConflictException:
            print('Access policy already exists')
    except Exception as e:
        print(f'Access policy setup warning (non-fatal): {e}')


def create_vector_index(endpoint, region):
    """Create HNSW vector index in AOSS collection using opensearch-py"""
    from opensearchpy import OpenSearch, RequestsHttpConnection
    from requests_aws4auth import AWS4Auth

    creds = boto3.Session().get_credentials()
    awsauth = AWS4Auth(
        creds.access_key, creds.secret_key, region, 'aoss',
        session_token=creds.token,
    )
    host = endpoint.replace('https://', '')
    client = OpenSearch(
        hosts=[{'host': host, 'port': 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=60,
    )
    index_body = {
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
    }
    result = client.indices.create(index='game-cs-index', body=index_body)
    print(f'Create index result: {result}')


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

            # Wait for index to propagate in AOSS before creating KB
            print('Waiting 30s for index propagation...')
            time.sleep(30)

            # Step 4: Create Knowledge Base (handle name conflict from orphaned KBs)
            print('Creating Knowledge Base...')
            bedrock = boto3.client('bedrock-agent')
            kb_name = props['KnowledgeBaseName']

            # Check if KB with same name exists (orphaned from previous failed deploy)
            kb_id = None
            existing_kbs = bedrock.list_knowledge_bases()
            for existing in existing_kbs.get('knowledgeBaseSummaries', []):
                if existing['name'] == kb_name:
                    old_id = existing['knowledgeBaseId']
                    old_status = existing['status']
                    print(f'Found existing KB: {old_id} (status: {old_status})')
                    if old_status in ('ACTIVE', 'DELETE_UNSUCCESSFUL'):
                        print(f'Reusing existing KB ({old_status}): {old_id}')
                        kb_id = old_id
                        break
                    elif old_status == 'CREATING':
                        # Wait for it to finish creating
                        for _ in range(30):
                            time.sleep(5)
                            check = bedrock.get_knowledge_base(knowledgeBaseId=old_id)
                            s = check['knowledgeBase']['status']
                            if s != 'CREATING':
                                if s == 'ACTIVE':
                                    kb_id = old_id
                                break
                        if kb_id:
                            break
                    else:
                        # Try to delete non-ACTIVE KB
                        try:
                            for ds in bedrock.list_data_sources(knowledgeBaseId=old_id).get('dataSourceSummaries', []):
                                bedrock.delete_data_source(knowledgeBaseId=old_id, dataSourceId=ds['dataSourceId'])
                                time.sleep(5)
                            bedrock.delete_knowledge_base(knowledgeBaseId=old_id)
                            time.sleep(15)
                        except Exception as del_err:
                            print(f'Delete old KB warning (non-fatal): {del_err}')

            if not kb_id:
                kb = bedrock.create_knowledge_base(
                    name=kb_name,
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

                # Wait for KB to become ACTIVE
                for _ in range(30):
                    kb_status = bedrock.get_knowledge_base(knowledgeBaseId=kb_id)['knowledgeBase']['status']
                    print(f'KB status: {kb_status}')
                    if kb_status == 'ACTIVE':
                        break
                    time.sleep(5)

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

            return {
                'PhysicalResourceId': kb_id,
                'Data': {'KnowledgeBaseId': kb_id, 'DataSourceId': ds_id},
            }

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
            return {'PhysicalResourceId': kb_id}

        else:  # Update
            return {
                'PhysicalResourceId': event.get('PhysicalResourceId', ''),
                'Data': {'KnowledgeBaseId': event.get('PhysicalResourceId', '')},
            }

    except Exception as e:
        print(f'ERROR: {e}')
        raise  # cr.Provider framework will catch and send FAILED response
