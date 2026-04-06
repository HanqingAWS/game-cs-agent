#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { GameCsAgentStack } from '../lib/game-cs-stack';

const app = new cdk.App();

new GameCsAgentStack(app, 'GameCsAgentStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEPLOY_REGION || 'us-east-1',
  },
  description: 'Game Customer Service AI Agent Demo Stack',
});

app.synth();
