import os
from functools import lru_cache

import boto3


@lru_cache
def _ssm():
    """Create and cache SSM client."""
    return boto3.client("ssm")


@lru_cache
def get_param(name, decrypt=True):
    """Get parameter from SSM Parameter Store."""
    resp = _ssm().get_parameter(Name=name, WithDecryption=decrypt)
    return resp["Parameter"]["Value"]


@lru_cache
def secrets():
    """Get WhatsApp verification token and app secret."""
    verify = get_param(os.environ["VERIFY_TOKEN"], decrypt=True)
    appsec = get_param(os.environ["APP_SECRET"], decrypt=True)
    whatsapp_number = get_param(os.environ["PHONE_ID"], decrypt=False)
    user_token = get_param(os.environ["USER_TOKEN"], decrypt=True)
    return verify, appsec, whatsapp_number, user_token


def _ok(body="OK"):
    """Return HTTP 200 response."""
    return {"statusCode": 200, "headers": {"Content-Type": "text/plain"}, "body": body}


def _bad_request(msg="Bad Request", code=400):
    """Return HTTP error response."""
    return {"statusCode": code, "headers": {"Content-Type": "text/plain"}, "body": msg}
