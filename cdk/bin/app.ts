#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { GameCsAgentStack } from '../lib/game-cs-stack';

const app = new cdk.App();

new GameCsAgentStack(app, 'GameCsAgentStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: 'us-east-1', // 固定使用 us-east-1 以支持最新的 Bedrock 模型
  },
  description: 'Game Customer Service AI Agent Demo Stack',
});

app.synth();
