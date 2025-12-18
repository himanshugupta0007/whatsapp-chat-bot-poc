import json
import os
import time

import boto3
from botocore.exceptions import ClientError

sqs = boto3.client("sqs")
ddb = boto3.resource("dynamodb")

OUTBOUND_QUEUE_URL = os.environ["OUTBOUND_QUEUE_URL"]
SESSIONS_TABLE = os.environ["BOT_SESSIONS_TABLE_NAME"]
MESSAGE_LOGS_TABLE = os.environ["BOT_MESSAGE_LOGS_TABLE_NAME"]

sessions_table = ddb.Table(SESSIONS_TABLE)
message_logs_table = ddb.Table(MESSAGE_LOGS_TABLE)

SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "7"))

def _now_epoch() -> int:
    return int(time.time())


def _session_ttl_epoch(days: int) -> int:
    return _now_epoch() + days * 24 * 60 * 60


def _safe_json_loads(s: str):
    try:
        return json.loads(s)
    except Exception:
        return None


def _normalize_user_text(message: dict) -> str:
    """
    Normalize inbound text from either:
    - message.type == text  -> message.text
    - message.type == interactive -> message.interactive (button_reply/list_reply)
    """
    mtype = (message or {}).get("type")

    if mtype == "text":
        return ((message.get("text") or "")).strip()

    if mtype == "interactive":
        inter = message.get("interactive") or {}
        # Meta Cloud: interactive: { "type": "button_reply", "button_reply": {"id":"", "title":""}}
        itype = inter.get("type")
        if itype == "button_reply":
            br = inter.get("button_reply") or {}
            return (br.get("id") or br.get("title") or "").strip()
        if itype == "list_reply":
            lr = inter.get("list_reply") or {}
            return (lr.get("id") or lr.get("title") or "").strip()

    return ""


def _is_hi_intent(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return t in {"hi", "hello", "hey", "start", "menu"} or t.startswith("hi ")


def _build_home_menu_message() -> dict:
    # WhatsApp interactive buttons (Meta Cloud API)
    return {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "Jai Shree Ram 🙏\nWelcome to Divya Sutra.\nWhat would you like to do?"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "MENU_BROWSE", "title": "Browse Products"}},
                    {"type": "reply", "reply": {"id": "MENU_CART", "title": "View Cart"}},
                    {"type": "reply", "reply": {"id": "MENU_TRACK", "title": "Track Order"}},
                ]
            },
        },
    }


def _upsert_session(whatsapp_number: str, state: str, context: dict | None = None):
    now = _now_epoch()
    sessions_table.put_item(
        Item={
            "whatsappNumber": whatsapp_number,
            "state": state,
            "context": context or {},
            "updatedAt": now,
            "ttl": _session_ttl_epoch(SESSION_TTL_DAYS),
        }
    )


def _mark_inbound_processed(provider_message_id: str, status: str = "PROCESSED"):
    if not provider_message_id:
        return
    key = {"messageKey": f"IN#{provider_message_id}"}
    try:
        message_logs_table.update_item(
            Key=key,
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status},
        )
    except ClientError:
        # Not critical; proceed
        return


def _send_outbound(to: str, outbound_payload: dict, idempotency_key: str):
    """
    Note: Outbound idempotency is enforced in whatsapp_sender (recommended).
    Here we just emit the message to OutboundQueue.
    """
    body = {
        "eventType": "OUTBOUND_MESSAGE",
        "to": to,
        "idempotencyKey": idempotency_key,
        "timestamp": _now_epoch(),
        "message": outbound_payload,
    }
    sqs.send_message(
        QueueUrl=OUTBOUND_QUEUE_URL,
        MessageBody=json.dumps(body),
    )


def _handle_record(record: dict):
    """
    record: SQS record
    record["body"]: normalized INBOUND_MESSAGE from WebhookIngestor
    """
    event_body = _safe_json_loads(record.get("body", ""))
    if not event_body:
        return

    whatsapp_number = event_body.get("whatsappNumber")
    provider_message_id = event_body.get("providerMessageId")
    message = event_body.get("message") or {}

    if not whatsapp_number:
        return

    user_text = _normalize_user_text(message)

    # Minimal flow: hi/start/menu => show HOME menu
    if _is_hi_intent(user_text):
        _upsert_session(whatsapp_number, state="HOME", context={})

        menu_msg = _build_home_menu_message()
        _send_outbound(
            to=whatsapp_number,
            outbound_payload=menu_msg,
            idempotency_key="HOME_MENU",
        )

        _mark_inbound_processed(provider_message_id, status="PROCESSED")
        return

    # Fallback: if unknown text, still show menu (for MVP)
    _upsert_session(whatsapp_number, state="HOME", context={})
    _send_outbound(
        to=whatsapp_number,
        outbound_payload=_build_home_menu_message(),
        idempotency_key="HOME_MENU",
    )
    _mark_inbound_processed(provider_message_id, status="PROCESSED")


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
