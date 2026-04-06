import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import * as path from 'path';
import { PythonFunction } from '@aws-cdk/aws-lambda-python-alpha';
import * as agentcore from '@aws-cdk/aws-bedrock-agentcore-alpha';

export class GameCsAgentStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ========== Cognito User Pool ==========
    const userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: 'game-cs-agent-users',
      selfSignUpEnabled: true,
      signInAliases: {
        email: true,
      },
      autoVerify: {
        email: true,
      },
      standardAttributes: {
        email: {
          required: true,
          mutable: true,
        },
      },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: false,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const userPoolClient = new cognito.UserPoolClient(this, 'UserPoolClient', {
      userPool,
      userPoolClientName: 'game-cs-agent-client',
      authFlows: {
        userSrp: true,
        userPassword: true,
      },
      generateSecret: false, // 前端使用，不生成 secret
      preventUserExistenceErrors: true,
    });

    // ========== DynamoDB Table ==========
    const rechargeTable = new dynamodb.Table(this, 'RechargeTable', {
      tableName: 'PlayerRechargeRecords',
      partitionKey: {
        name: 'player_id',
        type: dynamodb.AttributeType.STRING,
      },
      sortKey: {
        name: 'recharge_time',
        type: dynamodb.AttributeType.STRING,
      },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ========== S3 Bucket for Knowledge Base ==========
    const kbBucket = new s3.Bucket(this, 'KnowledgeBaseBucket', {
      bucketName: `game-cs-kb-${this.account}-${this.region}`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      versioned: false,
      encryption: s3.BucketEncryption.S3_MANAGED,
    });

    // 上传知识库文档
    new s3deploy.BucketDeployment(this, 'DeployKnowledgeBase', {
      sources: [s3deploy.Source.asset(path.join(__dirname, '../../knowledge-base'))],
      destinationBucket: kbBucket,
      destinationKeyPrefix: 'documents/',
    });

    // ========== Bedrock Knowledge Base ==========

    // Bedrock service role for KB
    const bedrockKbRole = new iam.Role(this, 'BedrockKbRole', {
      assumedBy: new iam.ServicePrincipal('bedrock.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonBedrockFullAccess'),
      ],
    });
    kbBucket.grantRead(bedrockKbRole);
    bedrockKbRole.addToPolicy(new iam.PolicyStatement({
      actions: ['aoss:APIAccessAll'],
      resources: ['*'],
    }));

    // Lambda role for KB creation (defined early for AOSS access policy)
    const createKbFunctionRole = new iam.Role(this, 'CreateKbFunctionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonBedrockFullAccess'),
      ],
      inlinePolicies: {
        KbCreation: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              actions: ['iam:PassRole'],
              resources: ['*'],
            }),
            new iam.PolicyStatement({
              actions: ['aoss:*'],
              resources: ['*'],
            }),
          ],
        }),
      },
    });

    // AOSS encryption policy
    const aossEncPolicy = new cdk.CfnResource(this, 'AossEncryptionPolicy', {
      type: 'AWS::OpenSearchServerless::SecurityPolicy',
      properties: {
        Name: 'game-cs-kb-enc',
        Type: 'encryption',
        Policy: JSON.stringify({
          Rules: [{ Resource: ['collection/game-cs-kb'], ResourceType: 'collection' }],
          AWSOwnedKey: true,
        }),
      },
    });

    // AOSS network policy
    const aossNetPolicy = new cdk.CfnResource(this, 'AossNetworkPolicy', {
      type: 'AWS::OpenSearchServerless::SecurityPolicy',
      properties: {
        Name: 'game-cs-kb-net',
        Type: 'network',
        Policy: JSON.stringify([{
          Rules: [
            { Resource: ['collection/game-cs-kb'], ResourceType: 'collection' },
            { Resource: ['collection/game-cs-kb'], ResourceType: 'dashboard' },
          ],
          AllowFromPublic: true,
        }]),
      },
    });

    // AOSS collection
    const aossCollection = new cdk.CfnResource(this, 'AossCollection', {
      type: 'AWS::OpenSearchServerless::Collection',
      properties: {
        Name: 'game-cs-kb',
        Type: 'VECTORSEARCH',
      },
    });
    aossCollection.addDependency(aossEncPolicy);
    aossCollection.addDependency(aossNetPolicy);

    // AOSS data access policy - use Fn::Sub to inject resolved role ARNs
    const aossAccessPolicy = new cdk.CfnResource(this, 'AossAccessPolicy', {
      type: 'AWS::OpenSearchServerless::AccessPolicy',
      properties: {
        Name: 'game-cs-kb-access',
        Type: 'data',
        Policy: cdk.Fn.sub(
          '[{"Rules":[{"Resource":["collection/game-cs-kb"],"Permission":["aoss:*"],"ResourceType":"collection"},{"Resource":["index/game-cs-kb/*"],"Permission":["aoss:*"],"ResourceType":"index"}],"Principal":["${BedrockRole}","${LambdaRole}"]}]',
          {
            BedrockRole: bedrockKbRole.roleArn,
            LambdaRole: createKbFunctionRole.roleArn,
          },
        ),
      },
    });

    // KB creation Lambda (external file to avoid inline escaping issues)
    const createKbFunction = new lambda.Function(this, 'CreateKbFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambda/create-kb')),
      timeout: cdk.Duration.minutes(10),
      memorySize: 256,
      role: createKbFunctionRole,
    });

    const knowledgeBaseProvider = new cr.Provider(this, 'KnowledgeBaseProvider', {
      onEventHandler: createKbFunction,
    });

    const knowledgeBase = new cdk.CustomResource(this, 'KnowledgeBase', {
      serviceToken: knowledgeBaseProvider.serviceToken,
      properties: {
        KnowledgeBaseName: 'game-cs-kb',
        RoleArn: bedrockKbRole.roleArn,
        BucketArn: kbBucket.bucketArn,
        Region: this.region,
        CollectionArn: aossCollection.getAtt('Arn').toString(),
        CollectionEndpoint: aossCollection.getAtt('CollectionEndpoint').toString(),
        LambdaRoleArn: createKbFunctionRole.roleArn,
      },
    });
    knowledgeBase.node.addDependency(aossAccessPolicy);

    const knowledgeBaseId = knowledgeBase.getAttString('KnowledgeBaseId');

    // ========== Recharge Query Lambda ==========
    const rechargeQueryFunction = new lambda.Function(this, 'RechargeQueryFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambda/recharge-query')),
      timeout: cdk.Duration.seconds(30),
      environment: {
        TABLE_NAME: rechargeTable.tableName,
      },
    });

    rechargeTable.grantReadData(rechargeQueryFunction);

    // ========== AgentCore Gateway ==========
    // 使用 AWS IAM 授权进行 Gateway 访问控制
    // 注意: 也可以使用 Cognito JWT 授权，详见下方注释
    const gateway = new agentcore.Gateway(this, 'AgentCoreGateway', {
      gatewayName: 'game-cs-gateway',
      // 使用 IAM 授权（适合 Lambda 到 Gateway 的服务间调用）
      authorizerConfiguration: agentcore.GatewayAuthorizer.usingAwsIam(),
    });

    // 添加 Lambda 目标，暴露为 MCP 工具
    gateway.addLambdaTarget('RechargeQuery', {
      lambdaFunction: rechargeQueryFunction,
      gatewayTargetName: 'recharge-query',
      description: '查询玩家充值记录工具',
      toolSchema: agentcore.ToolSchema.fromInline([{
        name: 'query_player_recharge',
        description: '查询玩家的充值历史记录，支持按日期范围过滤',
        inputSchema: {
          type: agentcore.SchemaDefinitionType.OBJECT,
          properties: {
            player_id: {
              type: agentcore.SchemaDefinitionType.STRING,
              description: '玩家ID，例如 player_001',
            },
            start_date: {
              type: agentcore.SchemaDefinitionType.STRING,
              description: '开始日期，ISO 8601 格式（可选），例如 2024-01-01T00:00:00Z',
            },
            end_date: {
              type: agentcore.SchemaDefinitionType.STRING,
              description: '结束日期，ISO 8601 格式（可选），例如 2024-12-31T23:59:59Z',
            },
          },
          required: ['player_id'],
        },
      }]),
    });

    // 获取 Gateway URL（非空断言，Gateway 创建后 URL 必定存在）
    const agentcoreGatewayUrl = gateway.gatewayUrl!;

    // ===== 可选: 使用 Cognito JWT 授权替代 IAM =====
    // 如需使用 Cognito JWT 授权，替换上面的 Gateway 创建为：
    //
    // const gateway = new agentcore.Gateway(this, 'AgentCoreGateway', {
    //   gatewayName: 'game-cs-gateway',
    //   authorizerConfiguration: agentcore.GatewayAuthorizer.usingJwt({
    //     discoveryUrl: `https://cognito-idp.${this.region}.amazonaws.com/${userPool.userPoolId}/.well-known/openid-configuration`,
    //     allowedAudiences: [userPoolClient.userPoolClientId],
    //   }),
    // });
    //
    // 然后在 agent Lambda 中，需要从 API Gateway 事件中提取用户的 JWT token
    // 并将其传递给 MCP 客户端作为 Bearer token

    // ========== Strands Agent Lambda ==========
    const agentFunction = new lambda.Function(this, 'AgentFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambda/agent')),
      timeout: cdk.Duration.minutes(5),
      memorySize: 1024,
      environment: {
        KNOWLEDGE_BASE_ID: knowledgeBaseId,
        AGENTCORE_GATEWAY_URL: agentcoreGatewayUrl,
        AWS_REGION_NAME: this.region,
      },
      logRetention: logs.RetentionDays.ONE_WEEK,
    });

    // 授予 Bedrock 权限
    agentFunction.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'bedrock:InvokeModel',
        'bedrock:InvokeModelWithResponseStream',
      ],
      resources: [
        `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0`,
      ],
    }));

    // 授予 Bedrock Agent Runtime 权限
    agentFunction.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'bedrock:Retrieve',
        'bedrock:RetrieveAndGenerate',
      ],
      resources: [
        `arn:aws:bedrock:${this.region}:${this.account}:knowledge-base/${knowledgeBaseId}`,
      ],
    }));

    // 授予调用 AgentCore Gateway 的权限
    gateway.grantInvoke(agentFunction);

    // ========== API Gateway with Streaming ==========
    const api = new apigateway.RestApi(this, 'GameCsApi', {
      restApiName: 'Game CS Agent API',
      description: 'API for Game Customer Service Agent',
      deployOptions: {
        stageName: 'prod',
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
        dataTraceEnabled: true,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['*'],
      },
    });

    // Cognito Authorizer
    const authorizer = new apigateway.CognitoUserPoolsAuthorizer(this, 'ApiAuthorizer', {
      cognitoUserPools: [userPool],
    });

    // /chat endpoint
    const chatResource = api.root.addResource('chat');

    // Lambda Integration with Response Streaming
    // 注意: API Gateway REST API 的 Lambda Response Streaming 支持需要特殊配置
    // 这里使用标准的 Lambda Proxy Integration
    const integration = new apigateway.LambdaIntegration(agentFunction, {
      proxy: true,
    });

    chatResource.addMethod('POST', integration, {
      authorizer,
      authorizationType: apigateway.AuthorizationType.COGNITO,
    });

    // ========== Frontend S3 Bucket + CloudFront ==========
    const websiteBucket = new s3.Bucket(this, 'WebsiteBucket', {
      websiteIndexDocument: 'index.html',
      publicReadAccess: false,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // CloudFront Origin Access Identity
    const originAccessIdentity = new cloudfront.OriginAccessIdentity(this, 'OAI');
    websiteBucket.grantRead(originAccessIdentity);

    // CloudFront Distribution
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      defaultRootObject: 'index.html',
      defaultBehavior: {
        origin: new origins.S3Origin(websiteBucket, {
          originAccessIdentity,
        }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
    });

    // 部署前端文件
    new s3deploy.BucketDeployment(this, 'DeployWebsite', {
      sources: [s3deploy.Source.asset(path.join(__dirname, '../../frontend'))],
      destinationBucket: websiteBucket,
      distribution,
      distributionPaths: ['/*'],
    });

    // ========== Seed Data Custom Resource ==========
    const seedDataFunction = new lambda.Function(this, 'SeedDataFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambda/seed-data')),
      timeout: cdk.Duration.minutes(2),
    });

    rechargeTable.grantReadWriteData(seedDataFunction);

    const seedDataProvider = new cr.Provider(this, 'SeedDataProvider', {
      onEventHandler: seedDataFunction,
    });

    new cdk.CustomResource(this, 'SeedData', {
      serviceToken: seedDataProvider.serviceToken,
      properties: {
        TableName: rechargeTable.tableName,
      },
    });

    // ========== Test User Creation ==========
    const createUserFunction = new lambda.Function(this, 'CreateUserFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromInline(`
import boto3
import cfnresponse
import json

cognito = boto3.client('cognito-idp')

def handler(event, context):
    print(f'Event: {json.dumps(event)}')

    request_type = event['RequestType']
    props = event['ResourceProperties']

    try:
        if request_type == 'Create':
            user_pool_id = props['UserPoolId']
            username = props['Username']
            password = props['Password']
            email = props['Email']

            # 创建用户
            cognito.admin_create_user(
                UserPoolId=user_pool_id,
                Username=username,
                UserAttributes=[
                    {'Name': 'email', 'Value': email},
                    {'Name': 'email_verified', 'Value': 'true'},
                ],
                MessageAction='SUPPRESS',
                TemporaryPassword=password
            )

            # 设置永久密码
            cognito.admin_set_user_password(
                UserPoolId=user_pool_id,
                Username=username,
                Password=password,
                Permanent=True
            )

            response_data = {'Username': username}
            cfnresponse.send(event, context, cfnresponse.SUCCESS, response_data, username)

        elif request_type == 'Delete':
            user_pool_id = props['UserPoolId']
            username = event['PhysicalResourceId']

            try:
                cognito.admin_delete_user(
                    UserPoolId=user_pool_id,
                    Username=username
                )
            except:
                pass

            cfnresponse.send(event, context, cfnresponse.SUCCESS, {}, username)

        else:  # Update
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {}, event['PhysicalResourceId'])

    except Exception as e:
        print(f'Error: {str(e)}')
        cfnresponse.send(event, context, cfnresponse.FAILED, {'Error': str(e)})
      `),
      timeout: cdk.Duration.seconds(30),
    });

    createUserFunction.addToRolePolicy(new iam.PolicyStatement({
      actions: [
        'cognito-idp:AdminCreateUser',
        'cognito-idp:AdminSetUserPassword',
        'cognito-idp:AdminDeleteUser',
      ],
      resources: [userPool.userPoolArn],
    }));

    const createUserProvider = new cr.Provider(this, 'CreateUserProvider', {
      onEventHandler: createUserFunction,
    });

    new cdk.CustomResource(this, 'TestUser', {
      serviceToken: createUserProvider.serviceToken,
      properties: {
        UserPoolId: userPool.userPoolId,
        Username: 'testuser',
        Password: 'TestUser123!',
        Email: 'testuser@example.com',
      },
    });

    // ========== Outputs ==========
    new cdk.CfnOutput(this, 'CloudFrontURL', {
      value: `https://${distribution.distributionDomainName}`,
      description: 'Frontend URL',
    });

    new cdk.CfnOutput(this, 'UserPoolId', {
      value: userPool.userPoolId,
      description: 'Cognito User Pool ID',
    });

    new cdk.CfnOutput(this, 'UserPoolClientId', {
      value: userPoolClient.userPoolClientId,
      description: 'Cognito User Pool Client ID',
    });

    new cdk.CfnOutput(this, 'ApiUrl', {
      value: api.url,
      description: 'API Gateway URL',
    });

    new cdk.CfnOutput(this, 'TestUsername', {
      value: 'testuser',
      description: 'Test user username',
    });

    new cdk.CfnOutput(this, 'TestPassword', {
      value: 'TestUser123!',
      description: 'Test user password',
    });

    new cdk.CfnOutput(this, 'KnowledgeBaseId', {
      value: knowledgeBaseId,
      description: 'Bedrock Knowledge Base ID',
    });

    new cdk.CfnOutput(this, 'AgentCoreGatewayUrl', {
      value: agentcoreGatewayUrl,
      description: 'AgentCore Gateway URL (MCP Endpoint)',
    });
  }
}
