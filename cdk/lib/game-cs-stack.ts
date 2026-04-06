import * as cdk from 'aws-cdk-lib';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as cloudfrontOrigins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as cr from 'aws-cdk-lib/custom-resources';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecr_assets from 'aws-cdk-lib/aws-ecr-assets';
import * as elbv2 from 'aws-cdk-lib/aws-elasticloadbalancingv2';
import * as ecsPatterns from 'aws-cdk-lib/aws-ecs-patterns';
import { Construct } from 'constructs';
import * as path from 'path';
import * as agentcore from '@aws-cdk/aws-bedrock-agentcore-alpha';

export class GameCsAgentStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // ========== Cognito User Pool ==========
    const userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: 'game-cs-agent-users',
      selfSignUpEnabled: true,
      signInAliases: { email: true },
      autoVerify: { email: true },
      standardAttributes: {
        email: { required: true, mutable: true },
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
      authFlows: { userSrp: true, userPassword: true },
      generateSecret: false,
      preventUserExistenceErrors: true,
    });

    // ========== DynamoDB Table ==========
    const rechargeTable = new dynamodb.Table(this, 'RechargeTable', {
      tableName: 'PlayerRechargeRecords',
      partitionKey: { name: 'player_id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'recharge_time', type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // ========== S3 Bucket for Knowledge Base ==========
    const kbBucket = new s3.Bucket(this, 'KnowledgeBaseBucket', {
      bucketName: `game-cs-kb-${this.account}-${this.region}`,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      encryption: s3.BucketEncryption.S3_MANAGED,
    });

    new s3deploy.BucketDeployment(this, 'DeployKnowledgeBase', {
      sources: [s3deploy.Source.asset(path.join(__dirname, '../../knowledge-base'))],
      destinationBucket: kbBucket,
      destinationKeyPrefix: 'documents/',
    });

    // ========== Bedrock Knowledge Base ==========
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

    // AOSS resources
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

    const aossCollection = new cdk.CfnResource(this, 'AossCollection', {
      type: 'AWS::OpenSearchServerless::Collection',
      properties: { Name: 'game-cs-kb', Type: 'VECTORSEARCH' },
    });
    aossCollection.addDependency(aossEncPolicy);
    aossCollection.addDependency(aossNetPolicy);

    // CreateKb Lambda role
    const createKbFunctionRole = new iam.Role(this, 'CreateKbFunctionRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonBedrockFullAccess'),
      ],
      inlinePolicies: {
        KbCreation: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({ actions: ['iam:PassRole'], resources: ['*'] }),
            new iam.PolicyStatement({ actions: ['aoss:*'], resources: ['*'] }),
          ],
        }),
      },
    });

    // AOSS data access policy
    const aossAccessPolicy = new cdk.CfnResource(this, 'AossAccessPolicy', {
      type: 'AWS::OpenSearchServerless::AccessPolicy',
      properties: {
        Name: 'game-cs-kb-access',
        Type: 'data',
        Policy: cdk.Fn.sub(
          '[{"Rules":[{"Resource":["collection/game-cs-kb"],"Permission":["aoss:*"],"ResourceType":"collection"},{"Resource":["index/game-cs-kb/*"],"Permission":["aoss:*"],"ResourceType":"index"}],"Principal":["${BedrockRole}","${LambdaRole}"]}]',
          { BedrockRole: bedrockKbRole.roleArn, LambdaRole: createKbFunctionRole.roleArn },
        ),
      },
    });

    // KB creation Lambda
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
        KnowledgeBaseName: `whiteout-survival-kb-${this.region}`,
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

    // ========== Recharge Query Lambda (MCP Tool) ==========
    const rechargeQueryFunction = new lambda.Function(this, 'RechargeQueryFunction', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.lambda_handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambda/recharge-query')),
      timeout: cdk.Duration.seconds(30),
      environment: { TABLE_NAME: rechargeTable.tableName },
    });
    rechargeTable.grantReadData(rechargeQueryFunction);

    // ========== AgentCore Gateway (MCP Tools) ==========
    const gateway = new agentcore.Gateway(this, 'AgentCoreGateway', {
      gatewayName: 'game-cs-gateway',
      authorizerConfiguration: agentcore.GatewayAuthorizer.usingAwsIam(),
    });

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
              description: '开始日期，ISO 8601 格式（可选）',
            },
            end_date: {
              type: agentcore.SchemaDefinitionType.STRING,
              description: '结束日期，ISO 8601 格式（可选）',
            },
          },
          required: ['player_id'],
        },
      }]),
    });

    const agentcoreGatewayUrl = gateway.gatewayUrl!;

    // ========== AgentCore Runtime (Strands Agent) ==========
    const agentRuntime = new agentcore.Runtime(this, 'AgentRuntime', {
      runtimeName: 'game_cs_agent_runtime',
      agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromCodeAsset({
        path: path.join(__dirname, '../../runtime'),
        runtime: agentcore.AgentCoreRuntime.PYTHON_3_12,
        entrypoint: ['main.py'],
      }),
      environmentVariables: {
        KNOWLEDGE_BASE_ID: knowledgeBaseId,
        AGENTCORE_GATEWAY_URL: agentcoreGatewayUrl,
        AWS_REGION_NAME: this.region,
      },
      authorizerConfiguration: agentcore.RuntimeAuthorizerConfiguration.usingCognito(
        userPool, [userPoolClient],
      ),
      protocolConfiguration: agentcore.ProtocolType.HTTP,
    });

    // Grant Bedrock permissions to Runtime
    agentRuntime.grantInvoke(new iam.ServicePrincipal('ecs-tasks.amazonaws.com'));

    const runtimeEndpoint = agentRuntime.addEndpoint('production');

    // ========== VPC + ECS Fargate (Web Service) ==========
    const vpc = new ec2.Vpc(this, 'WebVpc', {
      maxAzs: 2,
      natGateways: 1,
    });

    const cluster = new ecs.Cluster(this, 'WebCluster', { vpc });

    // Docker image for web service
    const webImage = new ecr_assets.DockerImageAsset(this, 'WebImage', {
      directory: path.join(__dirname, '../../'),
      file: 'web/Dockerfile',
    });

    // Fargate service with ALB
    const fargateService = new ecsPatterns.ApplicationLoadBalancedFargateService(
      this, 'WebService', {
        cluster,
        cpu: 256,
        memoryLimitMiB: 512,
        desiredCount: 1,
        taskImageOptions: {
          image: ecs.ContainerImage.fromDockerImageAsset(webImage),
          containerPort: 8080,
          environment: {
            AGENT_RUNTIME_ARN: agentRuntime.agentRuntimeArn,
            AGENT_RUNTIME_ENDPOINT_ARN: runtimeEndpoint.agentRuntimeEndpointArn,
            AWS_REGION_NAME: this.region,
            COGNITO_USER_POOL_ID: userPool.userPoolId,
            COGNITO_CLIENT_ID: userPoolClient.userPoolClientId,
          },
        },
        publicLoadBalancer: true,
        listenerPort: 80,
      },
    );

    // Grant ECS task permission to invoke AgentCore Runtime
    fargateService.taskDefinition.taskRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: [
          'bedrock-agentcore:InvokeAgentRuntime',
          'bedrock-agentcore:*',
          'bedrock:*',
        ],
        resources: ['*'],
      }),
    );

    // Health check
    fargateService.targetGroup.configureHealthCheck({
      path: '/health',
      healthyHttpCodes: '200',
    });

    // ALB security group - restrict to CloudFront IPs only
    const albSg = fargateService.loadBalancer.connections.securityGroups[0];
    // Remove default allow-all and add CloudFront prefix list
    // CloudFront managed prefix list - lookup by name
    const cfPrefixList = ec2.PrefixList.fromLookup(this, 'CloudFrontPrefixList', {
      prefixListName: 'com.amazonaws.global.cloudfront.origin-facing',
    });
    albSg.addIngressRule(
      ec2.Peer.prefixList(cfPrefixList.prefixListId),
      ec2.Port.tcp(80),
      'Allow CloudFront only',
    );

    // ========== CloudFront (CDN, optional) ==========
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      defaultBehavior: {
        origin: new cloudfrontOrigins.LoadBalancerV2Origin(fargateService.loadBalancer, {
          protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
        }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER,
      },
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
      properties: { TableName: rechargeTable.tableName },
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
            cognito.admin_create_user(
                UserPoolId=user_pool_id, Username=username,
                UserAttributes=[
                    {'Name': 'email', 'Value': email},
                    {'Name': 'email_verified', 'Value': 'true'},
                ],
                MessageAction='SUPPRESS', TemporaryPassword=password
            )
            cognito.admin_set_user_password(
                UserPoolId=user_pool_id, Username=username,
                Password=password, Permanent=True
            )
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {'Username': username}, username)
        elif request_type == 'Delete':
            try:
                cognito.admin_delete_user(
                    UserPoolId=props['UserPoolId'], Username=event['PhysicalResourceId']
                )
            except: pass
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {}, event['PhysicalResourceId'])
        else:
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
        Username: 'testuser@example.com',
        Password: 'TestUser123!',
        Email: 'testuser@example.com',
      },
    });

    // ========== Outputs ==========
    new cdk.CfnOutput(this, 'CloudFrontURL', {
      value: `https://${distribution.distributionDomainName}`,
      description: 'Frontend URL (via CloudFront)',
    });

    new cdk.CfnOutput(this, 'ALBUrl', {
      value: `http://${fargateService.loadBalancer.loadBalancerDnsName}`,
      description: 'ALB URL (direct access)',
    });

    new cdk.CfnOutput(this, 'UserPoolId', {
      value: userPool.userPoolId,
      description: 'Cognito User Pool ID',
    });

    new cdk.CfnOutput(this, 'UserPoolClientId', {
      value: userPoolClient.userPoolClientId,
      description: 'Cognito User Pool Client ID',
    });

    new cdk.CfnOutput(this, 'TestUsername', {
      value: 'testuser@example.com',
      description: 'Test user email',
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

    new cdk.CfnOutput(this, 'AgentRuntimeArn', {
      value: agentRuntime.agentRuntimeArn,
      description: 'AgentCore Runtime ARN',
    });
  }
}
