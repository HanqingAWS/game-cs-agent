"""
DynamoDB 数据初始化 Lambda 函数
作为 CDK Custom Resource 使用，在部署时填充测试数据
"""

import json
import boto3
import cfnresponse
from datetime import datetime, timedelta
from typing import List, Dict, Any

dynamodb = boto3.resource('dynamodb')


# 模拟充值记录数据
SEED_DATA = [
    {
        'player_id': 'player_001',
        'player_name': '星际探险家',
        'records': [
            {'days_ago': 1, 'amount': 6, 'item': '新手礼包', 'method': '微信支付'},
            {'days_ago': 3, 'amount': 30, 'item': '1000星钻', 'method': '支付宝'},
            {'days_ago': 7, 'amount': 98, 'item': '5000星钻', 'method': '微信支付'},
            {'days_ago': 15, 'amount': 198, 'item': '限定星舰皮肤套装', 'method': '支付宝'},
            {'days_ago': 20, 'amount': 50, 'item': '2500星钻', 'method': '微信支付'},
        ]
    },
    {
        'player_id': 'player_002',
        'player_name': '宇宙商人',
        'records': [
            {'days_ago': 2, 'amount': 30, 'item': '1000星钻', 'method': '支付宝'},
            {'days_ago': 5, 'amount': 98, 'item': '5000星钻', 'method': '支付宝'},
            {'days_ago': 12, 'amount': 328, 'item': '月卡套餐', 'method': '支付宝'},
            {'days_ago': 25, 'amount': 6, 'item': '新手礼包', 'method': '微信支付'},
            {'days_ago': 30, 'amount': 98, 'item': '5000星钻', 'method': '支付宝'},
            {'days_ago': 45, 'amount': 648, 'item': '年度会员', 'method': '支付宝'},
        ]
    },
    {
        'player_id': 'player_003',
        'player_name': '银河舰长',
        'records': [
            {'days_ago': 1, 'amount': 198, 'item': '10000星钻', 'method': 'PayPal'},
            {'days_ago': 8, 'amount': 98, 'item': '5000星钻', 'method': 'PayPal'},
            {'days_ago': 14, 'amount': 30, 'item': '1000星钻', 'method': 'PayPal'},
        ]
    },
    {
        'player_id': 'player_004',
        'player_name': '星际指挥官',
        'records': [
            {'days_ago': 3, 'amount': 6, 'item': '新手礼包', 'method': '微信支付'},
            {'days_ago': 10, 'amount': 30, 'item': '1000星钻', 'method': '微信支付'},
            {'days_ago': 18, 'amount': 98, 'item': '5000星钻', 'method': '支付宝'},
            {'days_ago': 25, 'amount': 198, 'item': '限定星舰皮肤套装', 'method': '支付宝'},
            {'days_ago': 35, 'amount': 50, 'item': '2500星钻', 'method': '微信支付'},
            {'days_ago': 40, 'amount': 328, 'item': '月卡套餐', 'method': '支付宝'},
            {'days_ago': 50, 'amount': 98, 'item': '5000星钻', 'method': '微信支付'},
            {'days_ago': 60, 'amount': 648, 'item': '年度会员', 'method': '支付宝'},
        ]
    },
    {
        'player_id': 'player_005',
        'player_name': '深空旅者',
        'records': [
            {'days_ago': 4, 'amount': 98, 'item': '5000星钻', 'method': '支付宝'},
            {'days_ago': 11, 'amount': 30, 'item': '1000星钻', 'method': '微信支付'},
            {'days_ago': 22, 'amount': 198, 'item': '10000星钻', 'method': '支付宝'},
            {'days_ago': 33, 'amount': 6, 'item': '新手礼包', 'method': '微信支付'},
            {'days_ago': 44, 'amount': 328, 'item': '月卡套餐', 'method': '支付宝'},
        ]
    }
]


def generate_recharge_records(table_name: str) -> List[Dict[str, Any]]:
    """
    生成充值记录数据

    Args:
        table_name: DynamoDB 表名

    Returns:
        生成的记录列表
    """

    records = []
    now = datetime.utcnow()

    for player in SEED_DATA:
        player_id = player['player_id']

        for record in player['records']:
            # 计算充值时间
            recharge_time = now - timedelta(days=record['days_ago'])

            item = {
                'player_id': player_id,
                'recharge_time': recharge_time.isoformat() + 'Z',
                'amount': record['amount'],
                'currency': 'CNY' if record['method'] in ['微信支付', '支付宝'] else 'USD',
                'payment_method': record['method'],
                'item_purchased': record['item'],
                'status': '成功'
            }

            records.append(item)

    return records


def seed_dynamodb_table(table_name: str) -> int:
    """
    向 DynamoDB 表中插入种子数据

    Args:
        table_name: 表名

    Returns:
        插入的记录数
    """

    table = dynamodb.Table(table_name)
    records = generate_recharge_records(table_name)

    # 批量写入
    with table.batch_writer() as batch:
        for record in records:
            batch.put_item(Item=record)

    print(f'成功插入 {len(records)} 条充值记录')
    return len(records)


def clear_dynamodb_table(table_name: str) -> int:
    """
    清空 DynamoDB 表数据

    Args:
        table_name: 表名

    Returns:
        删除的记录数
    """

    table = dynamodb.Table(table_name)
    count = 0

    # 扫描所有项目
    scan_kwargs = {
        'ProjectionExpression': 'player_id, recharge_time'
    }

    done = False
    start_key = None

    while not done:
        if start_key:
            scan_kwargs['ExclusiveStartKey'] = start_key

        response = table.scan(**scan_kwargs)
        items = response.get('Items', [])

        # 批量删除
        with table.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={
                    'player_id': item['player_id'],
                    'recharge_time': item['recharge_time']
                })
                count += 1

        start_key = response.get('LastEvaluatedKey', None)
        done = start_key is None

    print(f'成功删除 {count} 条记录')
    return count


def lambda_handler(event: Dict[str, Any], context: Any) -> None:
    """
    CloudFormation Custom Resource Lambda 处理函数

    事件类型:
    - Create: 创建资源时，插入种子数据
    - Update: 更新资源时，重新插入种子数据
    - Delete: 删除资源时，清空数据
    """

    print(f'收到事件: {json.dumps(event)}')

    # 获取请求类型和资源属性
    request_type = event.get('RequestType')
    resource_properties = event.get('ResourceProperties', {})
    table_name = resource_properties.get('TableName')

    response_data = {}
    physical_resource_id = f'SeedData-{table_name}'

    try:
        if not table_name:
            raise ValueError('缺少必需参数: TableName')

        if request_type == 'Create':
            # 创建: 插入种子数据
            count = seed_dynamodb_table(table_name)
            response_data = {
                'Message': f'成功插入 {count} 条充值记录',
                'RecordCount': count
            }
            cfnresponse.send(event, context, cfnresponse.SUCCESS,
                           response_data, physical_resource_id)

        elif request_type == 'Update':
            # 更新: 先清空再插入
            clear_dynamodb_table(table_name)
            count = seed_dynamodb_table(table_name)
            response_data = {
                'Message': f'成功更新数据，共 {count} 条记录',
                'RecordCount': count
            }
            cfnresponse.send(event, context, cfnresponse.SUCCESS,
                           response_data, physical_resource_id)

        elif request_type == 'Delete':
            # 删除: 清空数据（可选）
            # 注意：通常在删除 stack 时会删除整个表，所以这里可以跳过清空操作
            response_data = {
                'Message': '资源删除成功'
            }
            cfnresponse.send(event, context, cfnresponse.SUCCESS,
                           response_data, physical_resource_id)

        else:
            raise ValueError(f'不支持的请求类型: {request_type}')

    except Exception as e:
        print(f'错误: {str(e)}')
        response_data = {'Error': str(e)}
        cfnresponse.send(event, context, cfnresponse.FAILED,
                       response_data, physical_resource_id)
