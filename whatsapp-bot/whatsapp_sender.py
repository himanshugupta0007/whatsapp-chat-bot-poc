import json
import os
import time
import boto3
import urllib3
from botocore.exceptions import ClientError

http = urllib3.PoolManager()

ddb = boto3.resource("dynamodb")
ssm = boto3.client("ssm")

MESSAGE_LOGS_TABLE = os.environ["BOT_MESSAGE_LOGS_TABLE_NAME"]
PHONE_ID = os.environ["PHONE_ID"]
USER_TOKEN = os.environ["USER_TOKEN"]  # can be literal token OR SSM param name/path
GRAPH_BASE = os.environ.get("META_GRAPH_API_BASE_URL", "https://graph.facebook.com/v20.0")

table = ddb.Table(MESSAGE_LOGS_TABLE)

OUT_TTL_DAYS = int(os.environ.get("OUT_TTL_DAYS", "7"))


def _now_epoch() -> int:
    return int(time.time())


def _ttl_epoch(days: int) -> int:
    return _now_epoch() + days * 24 * 60 * 60


def _safe_json_loads(s: str):
    try:
        return json.loads(s)
    except Exception:
        return None


def _get_access_token() -> str:
    """
    If USER_TOKEN is an SSM parameter name/path, fetch it.
    Otherwise, treat it as literal access token.
    """
    token = (USER_TOKEN or "").strip()
    if token.startswith("/") or token.startswith("arn:aws:ssm:"):
        resp = ssm.get_parameter(Name=token, WithDecryption=True)
        return resp["Parameter"]["Value"]
    return token


def _build_send_payload(to: str, message: dict) -> dict:
    """
    Meta Cloud API expects:
    {
      "messaging_product": "whatsapp",
      "to": "9199...",
      "type": "text|interactive|template|image|...",
      "text": {...} OR "interactive": {...}
    }
    Our OutboundQueue message provides:
      message = { "type": "...", "text": "...", "interactive": {...} }
    """
    mtype = message.get("type")

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": mtype,
    }

    if mtype == "text":
        # Support either {"text":"hi"} or {"text":{"body":"hi"}}
        if isinstance(message.get("text"), str):
            payload["text"] = {"body": message["text"]}
        else:
            payload["text"] = message.get("text") or {"body": ""}

    elif mtype == "interactive":
        payload["interactive"] = message.get("interactive")

    elif mtype in ("image", "audio", "video", "document"):
        # If you later support media, message[mtype] should already be Meta format
        payload[mtype] = message.get(mtype)

    else:
        # Fallback: send as text
        payload["type"] = "text"
        payload["text"] = {"body": "Sorry, I couldn't understand that. Type 'menu' to see options."}

    return payload


def _log_outbound_once(message_key: str, item: dict) -> bool:
    """
    Conditional put to enforce idempotency.
    Returns True if inserted (send allowed), False if already exists (skip send).
    """
    try:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(messageKey)",
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _update_log_status(message_key: str, status: str, extra: dict | None = None):
    expr = "SET #s = :s, updatedAt = :u"
    names = {"#s": "status"}
    values = {":s": status, ":u": _now_epoch()}

    if extra:
        expr += ", meta = :m"
        values[":m"] = extra

    table.update_item(
        Key={"messageKey": message_key},
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


def _send_to_meta(payload: dict, access_token: str) -> dict:
    url = f"{GRAPH_BASE}/{PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    r = http.request(
        "POST",
        url,
        body=json.dumps(payload).encode("utf-8"),
        headers=headers,
        timeout=urllib3.Timeout(connect=5.0, read=15.0),
        retries=False,
    )

    body = r.data.decode("utf-8", errors="ignore")
    parsed = _safe_json_loads(body) or {"raw": body}

    return {"status": r.status, "body": parsed}


def _handle_record(record: dict):
    """
    record["body"] expected:
    {
      "eventType":"OUTBOUND_MESSAGE",
      "to":"9199...",
      "idempotencyKey":"HOME_MENU",
      "timestamp": 1734480000,
      "message": {...}
    }
    """
    body = _safe_json_loads(record.get("body", ""))
    if not body:
        return

    to = body.get("to")
    idem_key = body.get("idempotencyKey") or "NO_KEY"
    message = body.get("message") or {}

    if not to:
        return

    # Idempotency key for outbound log
    message_key = f"OUT#{to}#{idem_key}"

    # Create log item (idempotency gate)
    inserted = _log_outbound_once(
        message_key=message_key,
        item={
            "messageKey": message_key,
            "direction": "OUT",
            "status": "QUEUED",
            "to": to,
            "idempotencyKey": idem_key,
            "createdAt": _now_epoch(),
            "updatedAt": _now_epoch(),
            "ttl": _ttl_epoch(OUT_TTL_DAYS),
        },
    )

    if not inserted:
        # Already sent/queued earlier → skip (prevents duplicate sends on retries)
        return

    access_token = _get_access_token()
    send_payload = _build_send_payload(to=to, message=message)

    _update_log_status(message_key, "SENDING")

    resp = _send_to_meta(send_payload, access_token=access_token)

    if 200 <= resp["status"] < 300:
        _update_log_status(message_key, "SENT", extra=resp["body"])
        return

    # Retry-able vs non-retry-able
    # For MVP: treat 429 + 5xx as retryable by raising exception (SQS will retry)
    if resp["status"] in (429, 500, 502, 503, 504):
        _update_log_status(message_key, "RETRY", extra=resp["body"])
        raise RuntimeError(f"Meta send failed retryable: {resp['status']}")

    # Non-retryable: mark failed but don't raise (so message is removed)
    _update_log_status(message_key, "FAILED", extra=resp["body"])


def handler(event, context):
    """
    SQS batch handler with ReportBatchItemFailures.
    If one record fails, we return its messageId in batchItemFailures.
    """
    failures = []

    for record in event.get("Records", []):
        try:
            _handle_record(record)
        except Exception:
            failures.append({"itemIdentifier": record.get("messageId")})

    return {"batchItemFailures": failures}
