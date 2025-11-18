import logging

from config import secrets, _bad_request

logger = logging.getLogger()
logger.setLevel(logging.INFO)


# function to verify the webhook during setup
def get_handler(event, context):
    logger.info("Received event: %s", event)
    # API Gateway HTTP API passes queryStringParameters
    VERIFY_TOKEN, *_ = secrets()

    qp = (event.get("queryStringParameters") or {})
    mode = qp.get("hub.mode")
    token = qp.get("hub.verify_token")
    challenge = qp.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        # Meta expects raw challenge text
        logger.error("Webhook verified")
        return {"statusCode": 200, "headers": {"Content-Type": "text/plain"}, "body": challenge}

    return _bad_request("Verification failed", 403)
