"""
玩家充值记录查询 Lambda 函数
作为 MCP 工具通过 AgentCore Gateway 暴露给 Strands Agent
"""

import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional
import boto3
from boto3.dynamodb.conditions import Key

# DynamoDB 配置
TABLE_NAME = os.environ.get('TABLE_NAME', 'PlayerRechargeRecords')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(TABLE_NAME)


def query_player_recharge(
    player_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    查询玩家充值记录

    Args:
        player_id: 玩家ID
        start_date: 开始日期（ISO 8601格式，可选）
        end_date: 结束日期（ISO 8601格式，可选）

    Returns:
        充值记录列表
    """

    try:
        # 构建查询条件
        key_condition = Key('player_id').eq(player_id)

        # 如果指定了日期范围，添加排序键条件
        if start_date and end_date:
            key_condition = key_condition & Key('recharge_time').between(start_date, end_date)
        elif start_date:
            key_condition = key_condition & Key('recharge_time').gte(start_date)
        elif end_date:
            key_condition = key_condition & Key('recharge_time').lte(end_date)

        # 执行查询
        response = table.query(
            KeyConditionExpression=key_condition,
            ScanIndexForward=False  # 按时间倒序
        )

        items = response.get('Items', [])

        # 格式化结果
        records = []
        for item in items:
            records.append({
                'player_id': item['player_id'],
                'recharge_time': item['recharge_time'],
                'amount': float(item['amount']),
                'currency': item.get('currency', 'CNY'),
                'payment_method': item.get('payment_method', '未知'),
                'item_purchased': item.get('item_purchased', '未知'),
                'status': item.get('status', '成功')
            })

        return records

    except Exception as e:
        raise Exception(f'查询充值记录失败: {str(e)}')


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda 主处理函数
    处理来自 AgentCore Gateway 的 MCP 工具调用

    AgentCore Gateway 会将 MCP 工具调用转换为 Lambda 事件：
    {
        "tool": "query_player_recharge",
        "parameters": {
            "player_id": "player123",
            "start_date": "2024-01-01T00:00:00Z",
            "end_date": "2024-12-31T23:59:59Z"
        }
    }
    """

    print(f'收到请求: {json.dumps(event, ensure_ascii=False)}')

    try:
        # 解析请求参数
        if isinstance(event.get('body'), str):
            body = json.loads(event['body'])
        else:
            body = event.get('body', event)

        # 获取工具名称和参数
        tool_name = body.get('tool', '')
        parameters = body.get('parameters', {})

        # 验证工具名称
        if tool_name != 'query_player_recharge':
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': f'不支持的工具: {tool_name}'
                }, ensure_ascii=False)
            }

        # 获取参数
        player_id = parameters.get('player_id')
        start_date = parameters.get('start_date')
        end_date = parameters.get('end_date')

        # 验证必需参数
        if not player_id:
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': '缺少必需参数: player_id'
                }, ensure_ascii=False)
            }

        # 查询充值记录
        records = query_player_recharge(player_id, start_date, end_date)

        # 构建响应
        response_body = {
            'success': True,
            'player_id': player_id,
            'total_records': len(records),
            'records': records
        }

        # 如果有充值记录，计算总金额
        if records:
            total_amount = sum(r['amount'] for r in records)
            response_body['total_amount'] = total_amount
            response_body['currency'] = records[0]['currency']

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json'
            },
            'body': json.dumps(response_body, ensure_ascii=False)
        }

    except Exception as e:
        print(f'错误: {str(e)}')
        return {
            'statusCode': 500,
            'body': json.dumps({
                'success': False,
                'error': str(e)
            }, ensure_ascii=False)
        }
