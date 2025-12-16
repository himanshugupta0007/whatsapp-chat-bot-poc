from datetime import datetime
from typing import Any, Dict
import json

def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "body": body,
    }
