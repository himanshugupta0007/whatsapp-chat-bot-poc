import json
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

# --------------------------
# ENV
# --------------------------
ORDERS_TABLE = os.environ.get("ORDER_TABLE", "ds-orders")
CARTS_TABLE = os.environ.get("CARTS_TABLE", "ds-carts")  # optional integration
DEFAULT_SHIPPING = int(os.environ.get("DEFAULT_SHIPPING", "80"))
DEFAULT_TAX = int(os.environ.get("DEFAULT_TAX", "0"))
DEFAULT_CART_TTL_DAYS = int(os.environ.get("DEFAULT_CART_TTL_DAYS", "30"))

DDB = boto3.resource("dynamodb")
orders_table = DDB.Table(ORDERS_TABLE)
try:
    carts_table = DDB.Table(CARTS_TABLE) if CARTS_TABLE else None
except Exception as e:
    print(f"[ERROR] Failed to initialize carts_table: {e}")
    carts_table = None


# --------------------------
# Common utility (inlined)
# --------------------------
def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=str),
    }


def _now_epoch() -> int:
    return int(time.time())


def _ttl_epoch(days: int) -> int:
    return _now_epoch() + days * 24 * 60 * 60


# --------------------------
# Business helpers
# --------------------------
def calculate_totals(
        items: List[Dict[str, Any]],
        discount_amount: int = 0,
        shipping: int = 0,
        tax: int = 0,
        currency: str = "INR",
) -> Dict[str, Any]:
    sub_total = sum(int(item.get("lineTotal", 0)) for item in items)
    grand_total = sub_total - int(discount_amount) + int(shipping) + int(tax)

    return {
        "subTotal": int(sub_total),
        "discount": int(discount_amount),
        "shipping": int(shipping),
        "tax": int(tax),  # ✅ stored and returned for invoice
        "grandTotal": int(grand_total),
        "currency": currency,
    }


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _load_cart(cart_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not carts_table:
        print(f"[LOAD_CART][ERROR] carts_table is None, CARTS_TABLE={CARTS_TABLE}")
        return None

    try:
        # Best path: use primary key if userId is available
        if user_id:
            res = carts_table.get_item(Key={"userId": user_id, "cartId": cart_id})
            item = res.get("Item")
            if item:
                return item

        # Fallback: query GSI by cartId
        from boto3.dynamodb.conditions import Key

        res = carts_table.query(
            IndexName="CartIdIndex",
            KeyConditionExpression=Key("cartId").eq(cart_id),
            Limit=1,
        )
        items = res.get("Items", [])
        return items[0] if items else None

    except ClientError as e:
        print("[LOAD_CART][ERROR]", e.response.get("Error", {}))
        return None


def _mock_items() -> List[Dict[str, Any]]:
    return [
        {
            "productId": "prod_123",
            "name": "Mock Hawan Samagri Kit",
            "quantity": 2,
            "unitPrice": 599,
            "lineTotal": 2 * 599,
        }
    ]


# --------------------------
# Handlers
# --------------------------
def create_order(event: Dict[str, Any]) -> Dict[str, Any]:
    payload = event.get("payload") or {}

    cart_id = payload.get("cartId")
    user_id = payload.get("userId")
    whatsapp_number = payload.get("whatsappNumber")
    shipping_address = payload.get("shippingAddress") or {}
    payment_req = payload.get("payment") or {}
    metadata = payload.get("metadata") or {}

    if not cart_id:
        return response(400, {"message": "cartId is required"})
    if not user_id and not whatsapp_number:
        return response(400, {"message": "Either userId or whatsappNumber is required"})

    cart = _load_cart(cart_id, user_id)

    # Items & money inputs
    if cart and isinstance(cart.get("items"), list) and len(cart["items"]) > 0:
        items = cart["items"]
        currency = cart.get("currency") or "INR"
        discount_amount = _safe_int(cart.get("discount") or (cart.get("totals") or {}).get("discount"), 0)
        shipping = _safe_int(cart.get("shipping") or (cart.get("totals") or {}).get("shipping"), DEFAULT_SHIPPING)
        tax = _safe_int(cart.get("tax") or (cart.get("totals") or {}).get("tax"), DEFAULT_TAX)
    else:
        print("[WARN] Cart not found or invalid.")
        return response(500, {"message": "Something went wrong. Please try again"})

    totals = calculate_totals(
        items,
        discount_amount=discount_amount,
        shipping=shipping,
        tax=tax,
        currency=currency,
    )

    order_id = f"ord_{uuid.uuid4().hex[:10]}"
    order_number = f"DS-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
    now = now_iso()

    method = (payment_req.get("method") or "RAZORPAY").upper()

    if method == "RAZORPAY":
        payment = {
            "method": "RAZORPAY",
            "status": "PENDING",
            "razorpayOrderId": f"order_{uuid.uuid4().hex[:10]}",
            "razorpayPaymentId": None,
            "amount": totals["grandTotal"],
            "currency": totals["currency"],
        }
        status = "PENDING_PAYMENT"
    elif method == "COD":
        payment = {
            "method": "COD",
            "status": "PENDING",
            "razorpayOrderId": None,
            "razorpayPaymentId": None,
            "amount": totals["grandTotal"],
            "currency": totals["currency"],
        }
        status = "PROCESSING"
    else:
        payment = {
            "method": method,
            "status": "PENDING",
            "razorpayOrderId": None,
            "razorpayPaymentId": None,
            "amount": totals["grandTotal"],
            "currency": totals["currency"],
        }
        status = "PENDING_PAYMENT"

    order = {
        "orderId": order_id,
        "orderNumber": order_number,
        "cartId": cart_id,
        "userId": user_id,
        "whatsappNumber": whatsapp_number,
        "status": status,
        "items": items,
        "totals": totals,  # ✅ includes tax for invoice
        "shippingAddress": shipping_address,
        "payment": payment,
        "couponCode": "AUTO10" if discount_amount > 0 else None,
        "discountReason": "10% discount on orders above ₹1000" if discount_amount > 0 else None,
        "metadata": metadata,
        "createdAt": now,
        "updatedAt": now,
        # Useful for cleanup/analytics (optional)
        "ttl": _ttl_epoch(DEFAULT_CART_TTL_DAYS),
    }

    try:
        # ensure userId exists for GSI queries
        if not order.get("userId") and whatsapp_number:
            # you can still store without userId, but LIST_ORDERS by userId won't work.
            pass

        orders_table.put_item(
            Item=order,
            ConditionExpression="attribute_not_exists(orderId)",
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            return response(409, {"message": "Order already exists", "orderId": order_id})
        print("[CREATE_ORDER][ERROR]", e.response.get("Error", {}))
        return response(500, {"message": "Failed to create order", "error": str(e)})

    return response(201, order)


def get_order(event: Dict[str, Any]) -> Dict[str, Any]:
    order_id = event.get("orderId")
    if not order_id:
        return response(400, {"message": "orderId is required"})

    try:
        res = orders_table.get_item(Key={"orderId": order_id})
        item = res.get("Item")
        if not item:
            return response(404, {"message": "Order not found", "orderId": order_id})
        return response(200, item)
    except ClientError as e:
        print("[GET_ORDER][ERROR]", e.response.get("Error", {}))
        return response(500, {"message": "Failed to fetch order", "error": str(e)})


def list_orders(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Uses GSI UserOrdersIndex for userId or WhatsAppOrdersIndex for whatsappNumber.
    """
    filters = event.get("filters") or {}
    pagination = event.get("pagination") or {}

    user_id = filters.get("userId") or None
    whatsapp_number = filters.get("whatsappNumber") or None
    status_filter = filters.get("status") or None

    limit = max(1, min(int(pagination.get("limit", "10")), 50))
    next_token = pagination.get("nextToken")

    if not user_id and not whatsapp_number:
        return response(400, {"message": "Either filters.userId or filters.whatsappNumber is required"})

    eks = None
    if next_token:
        try:
            eks = json.loads(next_token)
        except Exception:
            return response(400, {"message": "pagination.nextToken must be valid JSON of LastEvaluatedKey"})

    try:
        from boto3.dynamodb.conditions import Key, Attr

        if user_id:
            query_kwargs = {
                "IndexName": "UserOrdersIndex",
                "KeyConditionExpression": Key("userId").eq(user_id),
            }
        else:
            query_kwargs = {
                "IndexName": "WhatsAppOrdersIndex",
                "KeyConditionExpression": Key("whatsappNumber").eq(whatsapp_number),
            }

        query_kwargs.update({
            "ScanIndexForward": False,
            "Limit": limit,
        })

        if eks:
            query_kwargs["ExclusiveStartKey"] = eks

        if status_filter:
            query_kwargs["FilterExpression"] = Attr("status").eq(status_filter)

        res = orders_table.query(**query_kwargs)

        items = res.get("Items", [])
        lek = res.get("LastEvaluatedKey")
        out = {
            "items": items,
            "limit": limit,
            "nextToken": json.dumps(lek) if lek else None,
        }
        return response(200, out)

    except ClientError as e:
        print("[LIST_ORDERS][ERROR]", e.response.get("Error", {}))
        return response(500, {"message": "Failed to list orders", "error": str(e)})

def update_order_status(event: Dict[str, Any]) -> Dict[str, Any]:
    order_id = event.get("orderId")
    payload = event.get("payload") or {}

    if not order_id:
        return response(400, {"message": "orderId is required"})

    new_status = payload.get("status")
    payment_update = payload.get("payment") or {}

    if not new_status and not payment_update:
        return response(400, {"message": "payload.status and/or payload.payment is required"})

    # Build dynamic update expression
    update_parts = []
    expr_vals: Dict[str, Any] = {":u": now_iso()}
    expr_names: Dict[str, str] = {"#updatedAt": "updatedAt"}

    update_parts.append("#updatedAt = :u")

    if new_status:
        expr_names["#status"] = "status"
        expr_vals[":s"] = new_status
        update_parts.append("#status = :s")

    if payment_update:
        expr_names["#payment"] = "payment"
        expr_vals[":p"] = payment_update
        update_parts.append("#payment = :p")

    update_expr = "SET " + ", ".join(update_parts)

    try:
        res = orders_table.update_item(
            Key={"orderId": order_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_vals,
            ConditionExpression="attribute_exists(orderId)",
            ReturnValues="ALL_NEW",
        )
        return response(200, res.get("Attributes", {}))

    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            return response(404, {"message": "Order not found", "orderId": order_id})
        print("[UPDATE_ORDER_STATUS][ERROR]", e.response.get("Error", {}))
        return response(500, {"message": "Failed to update order", "error": str(e)})


# --------------------------
# Lambda entrypoint
# --------------------------
def lambda_handler(event, context):
    print("Received event:", json.dumps(event))

    op = event.get("operation")

    if op == "CREATE_ORDER":
        return create_order(event)

    if op == "GET_ORDER":
        return get_order(event)

    if op == "LIST_ORDERS":
        return list_orders(event)

    if op == "UPDATE_ORDER_STATUS":
        return update_order_status(event)

    return response(400, {"message": f"Unknown operation: {op}"})
