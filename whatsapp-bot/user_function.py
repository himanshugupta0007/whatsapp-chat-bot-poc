import json
import uuid
from datetime import datetime
from typing import Dict, Any


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

    if not phone:
        return _response(400, {"message": "phone is required"})

    user_id = f"user_{uuid.uuid4().hex[:10]}"
    timestamp = _now_iso()

    # Mock DB representation
    user = {
        "userId": user_id,
        "phone": phone,
        "name": name,
        "channel": channel,
        "language": language,
        "createdAt": timestamp,
        "updatedAt": timestamp,
        "metadata": {},
    }

    # later: PutItem in ds-users
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

    # Mock (replace with DynamoDB GetItem)
    user = {
        "userId": user_id,
        "phone": "+919811112222",
        "name": "Mock User",
        "channel": "WHATSAPP",
        "language": "hi-IN",
        "createdAt": "2025-12-03T10:00:00Z",
        "updatedAt": "2025-12-03T10:10:00Z",
        "metadata": {
            "lastOrderId": "ord_001",
            "lastSeenMenu": "MAIN"
        }
    }

    return _response(200, user)


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

    # Mock existing user (later fetch from DynamoDB)
    user = {
        "userId": user_id,
        "phone": "+919811112222",
        "name": "Mock User",
        "channel": "WHATSAPP",
        "language": "hi-IN",
        "createdAt": "2025-12-03T10:00:00Z",
        "updatedAt": _now_iso(),
        "metadata": {}
    }

    # Apply updates
    if "name" in payload:
        user["name"] = payload["name"]

    if "language" in payload:
        user["language"] = payload["language"]

    # later: DynamoDB UpdateItem for ds-users
    return _response(200, user)


def get_user_by_phone(event: Dict[str, Any]):
    """
    operation: GET_USER_BY_PHONE
    {
      "operation": "GET_USER_BY_PHONE",
      "phone": "+9198xxxxxx"
    }
    """

    phone = event.get("phone")
    if not phone:
        return _response(400, {"message": "phone is required"})

    # Mock lookup (later use DynamoDB GSI on phone)
    if phone != "+919811112222":
        return _response(404, {"message": "User not found for phone"})

    user = {
        "userId": "user_mock123",
        "phone": phone,
        "name": "Mock User",
        "channel": "WHATSAPP",
        "language": "hi-IN",
        "createdAt": "2025-12-03T10:00:00Z",
        "updatedAt": "2025-12-03T10:10:00Z",
    }

    return _response(200, user)


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

    return _response(400, {"message": f"Unknown operation: {op}"})
