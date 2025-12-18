import json
import os
import time

import boto3
from botocore.exceptions import ClientError

from config import secrets

sqs = boto3.client("sqs")
ddb = boto3.resource("dynamodb")

INBOUND_QUEUE_URL = os.environ["INBOUND_QUEUE_URL"]
MESSAGE_LOGS_TABLE = os.environ["BOT_MESSAGE_LOGS_TABLE_NAME"]

message_logs_table = ddb.Table(MESSAGE_LOGS_TABLE)


def _now_epoch() -> int:
    return int(time.time())


def _response(status: int, body: dict | str):
    return {
        "statusCode": status,
        "body": body
    }


# ---------------------------------------------------------
# GET: Meta webhook verification
# ---------------------------------------------------------
def _handle_get(event):
    params = event.get("queryStringParameters") or {}

    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    VERIFY_TOKEN, *_ = secrets()

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/plain"},
            "body": challenge,
        }

    return _response(403, "Verification failed")


# ---------------------------------------------------------
# POST: Inbound WhatsApp messages
# ---------------------------------------------------------
def _handle_post(event):
    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON"})

    # Meta structure: entry -> changes -> value
    entries = payload.get("entry", [])
    if not entries:
        return _response(200, {"status": "ignored"})

    for entry in entries:
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])

            for msg in messages:
                _process_message(msg)

    # Always respond 200 to Meta
    return _response(200, {"status": "received"})


def _process_message(msg: dict):
    """
    Normalize inbound WhatsApp message and push to SQS
    """

    provider_message_id = msg.get("id")
    from_number = msg.get("from")
    timestamp = int(msg.get("timestamp", _now_epoch()))

    if not provider_message_id or not from_number:
        return

    message_key = f"IN#{provider_message_id}"

    # -----------------------------
    # Idempotency check
    # -----------------------------
    try:
        message_logs_table.put_item(
            Item={
                "messageKey": message_key,
                "direction": "IN",
                "status": "RECEIVED",
                "createdAt": timestamp,
                "ttl": timestamp + (7 * 24 * 60 * 60),  # 7 days
            },
            ConditionExpression="attribute_not_exists(messageKey)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            # Duplicate webhook
            return
        raise

    # -----------------------------
    # Normalize message
    # -----------------------------
    message_type = msg.get("type")

    normalized_message = {
        "type": message_type,
        "text": None,
        "interactive": None,
        "media": None,
    }

    if message_type == "text":
        normalized_message["text"] = msg.get("text", {}).get("body", "")

    elif message_type == "interactive":
        normalized_message["interactive"] = msg.get("interactive")

    elif message_type in ("image", "audio", "video", "document"):
        normalized_message["media"] = msg.get(message_type)

    event_payload = {
        "eventType": "INBOUND_MESSAGE",
        "provider": "whatsapp",
        "providerMessageId": provider_message_id,
        "whatsappNumber": from_number,
        "timestamp": timestamp,
        "message": normalized_message,
    }

    # -----------------------------
    # Push to SQS
    # -----------------------------
    sqs.send_message(
        QueueUrl=INBOUND_QUEUE_URL,
        MessageBody=json.dumps(event_payload),
    )

    # Update log as PROCESSED
    message_logs_table.update_item(
        Key={"messageKey": message_key},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "PROCESSED"},
    )


# ---------------------------------------------------------
# Lambda entrypoint
# ---------------------------------------------------------
def handler(event, context):
    method = event.get("requestContext", {}).get("httpMethod", {})

    if method == "GET":
        return _handle_get(event)

    if method == "POST":
        return _handle_post(event)

    return _response(405, {"error": "Method not allowed"})
