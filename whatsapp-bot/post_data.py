import base64
import hashlib
import hmac
import json
import logging
import urllib.request

from config import secrets, _ok, _bad_request

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# handler for incoming POST data (messages, etc.)
def post_handler(event, _ctx):
    logger.info("Received event: %s", event)

    _, APP_SECRET, *_ = secrets()

    body = event.get("body") or ""
    if not verify_signature(APP_SECRET, event):
        logger.error("Signature verification failed")
        return _bad_request(403, "Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.error("Invalid JSON body: %s", body)
        return _bad_request(400, "Invalid JSON")

    # basic router: echo + 2 simple commands
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    user = msg.get("from")
                    text = (msg.get("text", {}) or {}).get("body", "").strip().lower()
                    if not user: continue
                    if text == "menu":
                        _send_text(user, "Options:\n1) Order Kit\n2) Build Custom List\nType a message to try echo.")
                    elif text in ("hi", "hello", "namaste", "🙏"):
                        _send_text(user, "Namaste 🙏 How can I help?")
                    else:
                        _send_text(user, f"Echo: {text if text else '(no text)'}")
    except Exception as e:
        print("Router error:", e)

    return _ok("EVENT_RECEIVED")


def _send_text(to, text):
    _, _, PHONE_ID, USER_TOKEN = secrets()
    url = f"https://graph.facebook.com/v21.0/{PHONE_ID}/messages"
    data = json.dumps({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text[:4096]}
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {USER_TOKEN}")
    with urllib.request.urlopen(req, timeout=10) as resp:
        print("Send status:", resp.status)


def verify_signature(app_secret: str, event: str) -> bool:
    """
    Verify request using X-Hub-Signature-256 header and APP_SECRET.
    """
    if not app_secret:
        logger.warning("APP_SECRET not set, skipping signature verification")
        return True  # temporarily allow all for testing

    headers = event.get("headers") or {}
    headers_lower = {k.lower(): v for k, v in headers.items()}

    sig_header = headers_lower.get("x-hub-signature-256")
    if not sig_header:
        logger.warning("No X-Hub-Signature-256 header found")
        return False

    if sig_header.startswith("sha256="):
        received_sig = sig_header.split("=", 1)[1]
    else:
        received_sig = sig_header

    # Get raw body
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body).decode("utf-8")
        except Exception as e:
            logger.error("Failed to decode base64 body: %s", e)
            return False

    expected_hmac = hmac.new(
        app_secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    valid = hmac.compare_digest(expected_hmac, received_sig)

    if not valid:
        logger.warning(
            "Invalid signature.\nexpected=%s\nreceived=%s\nbody_snippet=%s",
            expected_hmac,
            received_sig,
            body[:200]
        )

    return valid
