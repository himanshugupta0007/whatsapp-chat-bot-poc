import json
import os
import uuid
from datetime import datetime
from typing import Dict, Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")

# Prefer env var; fall back to hard-coded name
USERS_TABLE = os.environ.get("USERS_TABLE", "ds-users")
users_table = dynamodb.Table(USERS_TABLE)


def _now_iso():
    return datetime.utcnow().isoformat() + "Z"


def _response(status_code: int, body: Dict[str, Any]):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }


def create_user(event: Dict[str, Any]):
    """
    operation: CREATE_USER
    payload:
    {
      "phone": "+9198xxxxxx",
      "name": "Himanshu",
      "channel": "WHATSAPP",
      "language": "hi-IN"
    }
    """

    payload = event.get("payload") or {}
    phone = payload.get("phone")
    name = payload.get("name") or None
    channel = payload.get("channel") or "UNKNOWN"
    language = payload.get("language") or "en-IN"
    stage = payload.get("stage") or "INITIAL"

    if not phone:
        return _response(400, {"message": "phone is required"})

    try:
        resp = users_table.query(
            IndexName="PhoneIndex",
            KeyConditionExpression=Key("phone").eq(phone),
            Limit=1,
        )
    except ClientError as e:
        print(f"[CREATE_USER] DynamoDB error querying phone: {e}")
        return _response(500, {"message": "Failed to verify phone uniqueness"})

    if resp.get("Items"):
        return _response(409, {"message": "Phone already registered"})

    user_id = f"user_{uuid.uuid4().hex[:10]}"
    timestamp = _now_iso()

    user = {
        "userId": user_id,
        "phone": phone,
        "name": name,
        "channel": channel,
        "language": language,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "metadata": {},
        "stage": stage,
    }

    try:
        # If you want to prevent accidental overwrite on same userId:
        # ConditionExpression="attribute_not_exists(userId)"
        users_table.put_item(Item=user)
    except ClientError as e:
        print(f"[CREATE_USER] DynamoDB error: {e}")
        return _response(500, {"message": "Failed to create user"})

    return _response(201, user)


def get_user(event: Dict[str, Any]):
    """
    operation: GET_USER
    {
      "operation": "GET_USER",
      "userId": "user_123"
    }
    """

    user_id = event.get("userId")
    if not user_id:
        return _response(400, {"message": "userId is required"})

    try:
        resp = users_table.get_item(Key={"userId": user_id})
    except ClientError as e:
        print(f"[GET_USER] DynamoDB error: {e}")
        return _response(500, {"message": "Failed to get user"})

    item = resp.get("Item")
    if not item:
        return _response(404, {"message": "User not found"})

    return _response(200, item)


def update_user(event: Dict[str, Any]):
    """
    operation: UPDATE_USER
    event:
    {
      "operation": "UPDATE_USER",
      "userId": "user_123",
      "payload": {
        "name": "New Name",
        "language": "en-IN"
      }
    }
    """

    user_id = event.get("userId")
    payload = event.get("payload") or {}

    if not user_id:
        return _response(400, {"message": "userId is required"})

    update_parts = []
    expr_attr_values = {}
    expr_attr_names = {}

    # Update name
    if "name" in payload:
        update_parts.append("#n = :name")
        expr_attr_names["#n"] = "name"
        expr_attr_values[":name"] = payload["name"]

    # Update language (reserved keyword)
    if "language" in payload:
        update_parts.append("#lang = :language")
        expr_attr_names["#lang"] = "language"
        expr_attr_values[":language"] = payload["language"]

    # Always update updatedAt
    update_parts.append("updatedAt = :updatedAt")
    expr_attr_values[":updatedAt"] = _now_iso()

    if not update_parts:
        return _response(400, {"message": "No fields to update"})

    update_expr = "SET " + ", ".join(update_parts)

    try:
        resp = users_table.update_item(
            Key={"userId": user_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_attr_names if expr_attr_names else None,
            ExpressionAttributeValues=expr_attr_values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        print(f"[UPDATE_USER] DynamoDB error: {e}")
        return _response(500, {"message": "Failed to update user"})

    updated_item = resp.get("Attributes", {})
    if not updated_item:
        # This happens if item didn’t exist
        return _response(404, {"message": "User not found"})

    return _response(200, updated_item)


def get_user_by_phone(event: Dict[str, Any]):
    """
    operation: GET_USER_BY_PHONE
    {
      "operation": "GET_USER_BY_PHONE",
      "phone": "+9198xxxxxx"
    }

    Requires GSI on "phone" with IndexName "phone-index"
    """

    phone = event.get("phone")
    if not phone:
        return _response(400, {"message": "phone is required"})

    try:
        resp = users_table.query(
            IndexName="PhoneIndex",
            KeyConditionExpression=Key("phone").eq(phone),
            Limit=1,
        )
    except ClientError as e:
        print(f"[GET_USER_BY_PHONE] DynamoDB error: {e}")
        return _response(500, {"message": "Failed to query user by phone"})

    items = resp.get("Items", [])
    if not items:
        return _response(404, {"message": "User not found for phone"})

    return _response(200, items[0])

def list_users(event: Dict[str, Any]):
    """
    operation: LIST_USERS
    {
      "operation": "LIST_USERS",
      "limit": 100,            # optional
      "lastKey": { ... }      # optional ExclusiveStartKey for pagination
    }
    """
    limit = event.get("limit")
    exclusive_start_key = event.get("lastKey")

    scan_kwargs = {}
    if limit:
        try:
            scan_kwargs["Limit"] = int(limit)
        except (TypeError, ValueError):
            return _response(400, {"message": "limit must be an integer"})

    if exclusive_start_key:
        scan_kwargs["ExclusiveStartKey"] = exclusive_start_key

    try:
        resp = users_table.scan(**scan_kwargs)
    except ClientError as e:
        print(f"[LIST_USERS] DynamoDB error: {e}")
        return _response(500, {"message": "Failed to list users"})

    items = resp.get("Items", [])
    result = {"items": items}
    if "LastEvaluatedKey" in resp:
        result["lastKey"] = resp["LastEvaluatedKey"]

    return _response(200, result)

def lambda_handler(event, context):
    """
    Expected event from API Gateway VTL:
    {
      "operation": "CREATE_USER",
      "userId": "...",
      "payload": { ... },
      "phone": "+91..."
    }
    """

    print("Received event:", json.dumps(event))

    op = event.get("operation")

    if op == "CREATE_USER":
        return create_user(event)

    if op == "GET_USER":
        return get_user(event)

    if op == "UPDATE_USER":
        return update_user(event)

    if op == "GET_USER_BY_PHONE":
        return get_user_by_phone(event)

    if op == "LIST_USERS":
        return list_users(event)

    return _response(400, {"message": f"Unknown operation: {op}"})
