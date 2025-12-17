import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

DDB = boto3.resource("dynamodb")
CARTS_TABLE = os.environ.get("CARTS_TABLE", "ds-carts")

DEFAULT_CART_TTL_DAYS = int(os.environ.get("DEFAULT_CART_TTL_DAYS", "30"))
DEFAULT_SHIPPING = int(os.environ.get("DEFAULT_SHIPPING", "0"))


def _now_epoch() -> int:
    return int(time.time())


def _ttl_epoch(days: int) -> int:
    return _now_epoch() + days * 24 * 60 * 60


def calculate_totals(
        items: List[Dict[str, Any]],
        discount_amount: int = 0,
        shipping: int = 0,
        tax_amount: int = 0,  # informational only (GST is included in item prices)
        currency: str = "INR",
) -> Dict[str, Any]:
    """
    GST-inclusive pricing:
    - subTotal = sum(lineTotal) (already includes GST)
    - grandTotal must NOT add tax again
    """
    sub_total = sum(int(item.get("lineTotal", 0)) for item in items)
    grand_total = sub_total - int(discount_amount) + int(shipping)

    return {
        "subTotal": sub_total,
        "discount": int(discount_amount),
        "shipping": int(shipping),
        "tax": int(tax_amount),  # for invoice display
        "taxIncluded": True,  # tells UI/invoice GST is included
        "grandTotal": grand_total,
        "currency": currency,
    }


def calculate_tax_inclusive(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Reverse GST out of GST-inclusive prices.
    Returns invoice-ready tax object.
    Each item should have:
      - lineTotal (required)
      - gstRate (e.g. 5, 12, 18). Defaults to 0 if missing.
    """
    tax_total = 0
    summary: Dict[str, int] = {}

    for it in items:
        gst_rate = float(it.get("gstRate", 0) or 0)
        gross = float(it.get("lineTotal", 0) or 0)

        if gst_rate <= 0 or gross <= 0:
            continue

        tax = gross * gst_rate / (100.0 + gst_rate)
        tax_int = int(round(tax))

        tax_total += tax_int
        key = str(int(gst_rate)) if gst_rate.is_integer() else str(gst_rate)
        summary[key] = summary.get(key, 0) + tax_int

    return {
        "included": True,
        "total": int(tax_total),
        "gstSummary": summary,  # {"5": 23, "12": 0}
        "type": "GST",
        "computedFrom": "GST_INCLUSIVE_PRICES",
    }


def _response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "body": body
    }


def _table():
    return DDB.Table(CARTS_TABLE)


def _auto_discount(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Rule: 10% off if subtotal >= 1000 (subtotal is GST-inclusive; OK for MVP)
    """
    sub_total = sum(int(i.get("lineTotal", 0)) for i in items)
    discount_amount = int(sub_total * 0.10) if sub_total >= 1000 else 0
    return {
        "couponCode": "AUTO10" if discount_amount > 0 else None,
        "discountReason": "10% discount on orders above ₹1000" if discount_amount > 0 else None,
        "discountAmount": discount_amount,
    }


def _find_item_index(items: List[Dict[str, Any]], item_id: str) -> int:
    for idx, it in enumerate(items):
        if it.get("itemId") == item_id:
            return idx
    return -1


def _get_cart_or_404(user_id: str, cart_id: str) -> Optional[Dict[str, Any]]:
    try:
        resp = _table().get_item(Key={"userId": user_id, "cartId": cart_id})
        return resp.get("Item")
    except ClientError:
        return None


def _recompute_cart(cart: Dict[str, Any], items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Recompute discount + derived GST + totals.
    Tax is derived from GST-inclusive prices and NOT added to grandTotal.
    """
    discount = _auto_discount(items)
    tax_obj = calculate_tax_inclusive(items)
    tax_amount = int(tax_obj.get("total", 0))

    currency = (cart.get("totals") or {}).get("currency", "INR")
    shipping_amount = int(cart.get("shippingAmount", DEFAULT_SHIPPING) or DEFAULT_SHIPPING)

    totals = calculate_totals(
        items,
        discount_amount=discount["discountAmount"],
        shipping=shipping_amount,
        tax_amount=tax_amount,
        currency=currency,
    )

    return {
        "items": items,
        "couponCode": discount["couponCode"],
        "discountReason": discount["discountReason"],
        "totals": totals,
        "tax": tax_obj,  # ✅ invoice-ready object
        "updatedAt": _now_epoch(),
        "expiresAt": _ttl_epoch(DEFAULT_CART_TTL_DAYS),
    }


def create_cart(event: Dict[str, Any]) -> Dict[str, Any]:
    user_id = event.get("userId") or "anonymous"
    source = event.get("source") or "UNKNOWN"

    cart_id = f"cart_{uuid.uuid4().hex[:8]}"
    items: List[Dict[str, Any]] = []

    discount = _auto_discount(items)
    tax_obj = calculate_tax_inclusive(items)

    totals = calculate_totals(
        items,
        discount_amount=discount["discountAmount"],
        shipping=DEFAULT_SHIPPING,
        tax_amount=int(tax_obj["total"]),
        currency="INR",
    )

    cart = {
        "userId": user_id,
        "cartId": cart_id,
        "source": source,
        "status": "ACTIVE",
        "items": items,
        "couponCode": discount["couponCode"],
        "discountReason": discount["discountReason"],
        "shippingAmount": DEFAULT_SHIPPING,
        "tax": tax_obj,  # ✅ invoice-ready
        "totals": totals,
        "createdAt": _now_epoch(),
        "updatedAt": _now_epoch(),
        "version": 1,
        "expiresAt": _ttl_epoch(DEFAULT_CART_TTL_DAYS),
    }

    try:
        _table().put_item(
            Item=cart,
            ConditionExpression="attribute_not_exists(userId) AND attribute_not_exists(cartId)",
        )
    except ClientError as e:
        return _response(500, {"message": "Failed to create cart", "error": str(e)})

    return _response(201, cart)


def get_cart(event: Dict[str, Any]) -> Dict[str, Any]:
    user_id = event.get("userId")
    cart_id = event.get("cartId")

    if not user_id or not cart_id:
        return _response(400, {"message": "userId and cartId are required"})

    cart = _get_cart_or_404(user_id, cart_id)
    if not cart:
        return _response(404, {"message": "Cart not found"})

    return _response(200, cart)


def add_item(event: Dict[str, Any]) -> Dict[str, Any]:
    cart_id = event.get("cartId")
    payload = event.get("payload") or {}
    user_id = payload.get("userId")

    if not user_id or not cart_id:
        return _response(400, {"message": "userId and cartId are required"})

    product_id = payload.get("productId")
    quantity = int(payload.get("quantity", 1))
    if not product_id:
        return _response(400, {"message": "productId is required"})
    if quantity < 1:
        return _response(400, {"message": "quantity must be >= 1"})

    # unitPrice is GST-inclusive
    unit_price = int(payload.get("unitPrice", 599))
    name = payload.get("name", "Mock Product From Catalog")
    gst_rate = int(payload.get("gstRate", 5))  # default 5% for your MVP catalog

    item_id = f"item_{uuid.uuid4().hex[:8]}"
    line_total = unit_price * quantity

    new_item = {
        "itemId": item_id,
        "productId": product_id,
        "name": name,
        "quantity": quantity,
        "unitPrice": unit_price,
        "lineTotal": line_total,
        "gstRate": gst_rate,
        "notes": payload.get("notes"),
        "customizationType": payload.get("customizationType", "STANDARD"),
    }

    cart = _get_cart_or_404(user_id, cart_id)
    if not cart:
        return _response(404, {"message": "Cart not found"})
    if cart.get("status") != "ACTIVE":
        return _response(409, {"message": "Cart is not ACTIVE"})

    items = cart.get("items") or []
    items.append(new_item)

    updates = _recompute_cart(cart, items)

    try:
        _table().update_item(
            Key={"userId": user_id, "cartId": cart_id},
            UpdateExpression=(
                "SET #items=:items, #coupon=:coupon, #discountReason=:dr, "
                "#totals=:totals, #tax=:tax, #updatedAt=:ua, #expiresAt=:ttl "
                "ADD #version :inc"
            ),
            ExpressionAttributeNames={
                "#items": "items",
                "#coupon": "couponCode",
                "#discountReason": "discountReason",
                "#totals": "totals",
                "#tax": "tax",
                "#updatedAt": "updatedAt",
                "#expiresAt": "expiresAt",
                "#status": "status",
                "#version": "version",
            },
            ExpressionAttributeValues={
                ":items": updates["items"],
                ":coupon": updates["couponCode"],
                ":dr": updates["discountReason"],
                ":totals": updates["totals"],
                ":tax": updates["tax"],
                ":ua": updates["updatedAt"],
                ":ttl": updates["expiresAt"],
                ":inc": 1,
                ":active": "ACTIVE",
            },
            ConditionExpression="#status = :active",
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        return _response(500, {"message": "Failed to add item", "error": str(e)})

    updated = _get_cart_or_404(user_id, cart_id)
    return _response(200, updated or {"message": "Updated but could not re-read cart"})


def update_item(event: Dict[str, Any]) -> Dict[str, Any]:
    user_id = event.get("userId")
    cart_id = event.get("cartId")
    item_id = event.get("itemId")
    payload = event.get("payload") or {}

    if not user_id or not cart_id or not item_id:
        return _response(400, {"message": "userId, cartId and itemId are required"})

    quantity = int(payload.get("quantity", 1))
    if quantity < 1:
        return _response(400, {"message": "quantity must be >= 1"})

    cart = _get_cart_or_404(user_id, cart_id)
    if not cart:
        return _response(404, {"message": "Cart not found"})
    if cart.get("status") != "ACTIVE":
        return _response(409, {"message": "Cart is not ACTIVE"})

    items = cart.get("items") or []
    idx = _find_item_index(items, item_id)
    if idx < 0:
        return _response(404, {"message": "Item not found in cart"})

    unit_price = int(items[idx].get("unitPrice", 0))
    items[idx]["quantity"] = quantity
    items[idx]["lineTotal"] = unit_price * quantity

    # allow updating gstRate if you ever need it
    if "gstRate" in payload:
        items[idx]["gstRate"] = int(payload["gstRate"])

    updates = _recompute_cart(cart, items)

    try:
        _table().update_item(
            Key={"userId": user_id, "cartId": cart_id},
            UpdateExpression=(
                "SET #items=:items, #coupon=:coupon, #discountReason=:dr, "
                "#totals=:totals, #tax=:tax, #updatedAt=:ua, #expiresAt=:ttl "
                "ADD #version :inc"
            ),
            ExpressionAttributeNames={
                "#items": "items",
                "#coupon": "couponCode",
                "#discountReason": "discountReason",
                "#totals": "totals",
                "#tax": "tax",
                "#updatedAt": "updatedAt",
                "#expiresAt": "expiresAt",
                "#status": "status",
                "#version": "version",
            },
            ExpressionAttributeValues={
                ":items": updates["items"],
                ":coupon": updates["couponCode"],
                ":dr": updates["discountReason"],
                ":totals": updates["totals"],
                ":tax": updates["tax"],
                ":ua": updates["updatedAt"],
                ":ttl": updates["expiresAt"],
                ":inc": 1,
                ":active": "ACTIVE",
            },
            ConditionExpression="#status = :active",
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        return _response(500, {"message": "Failed to update item", "error": str(e)})

    updated = _get_cart_or_404(user_id, cart_id)
    return _response(200, updated or {"message": "Updated but could not re-read cart"})


def remove_item(event: Dict[str, Any]) -> Dict[str, Any]:
    user_id = event.get("userId")
    cart_id = event.get("cartId")
    item_id = event.get("itemId")

    if not user_id or not cart_id or not item_id:
        return _response(400, {"message": "userId, cartId and itemId are required"})

    cart = _get_cart_or_404(user_id, cart_id)
    if not cart:
        return _response(404, {"message": "Cart not found"})
    if cart.get("status") != "ACTIVE":
        return _response(409, {"message": "Cart is not ACTIVE"})

    items = cart.get("items") or []
    idx = _find_item_index(items, item_id)
    if idx < 0:
        return _response(404, {"message": "Item not found in cart"})

    items.pop(idx)

    updates = _recompute_cart(cart, items)

    try:
        _table().update_item(
            Key={"userId": user_id, "cartId": cart_id},
            UpdateExpression=(
                "SET #items=:items, #coupon=:coupon, #discountReason=:dr, "
                "#totals=:totals, #tax=:tax, #updatedAt=:ua, #expiresAt=:ttl "
                "ADD #version :inc"
            ),
            ExpressionAttributeNames={
                "#items": "items",
                "#coupon": "couponCode",
                "#discountReason": "discountReason",
                "#totals": "totals",
                "#tax": "tax",
                "#updatedAt": "updatedAt",
                "#expiresAt": "expiresAt",
                "#status": "status",
                "#version": "version",
            },
            ExpressionAttributeValues={
                ":items": updates["items"],
                ":coupon": updates["couponCode"],
                ":dr": updates["discountReason"],
                ":totals": updates["totals"],
                ":tax": updates["tax"],
                ":ua": updates["updatedAt"],
                ":ttl": updates["expiresAt"],
                ":inc": 1,
                ":active": "ACTIVE",
            },
            ConditionExpression="#status = :active",
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        return _response(500, {"message": "Failed to remove item", "error": str(e)})

    updated = _get_cart_or_404(user_id, cart_id)
    return _response(200, updated or {"message": "Updated but could not re-read cart"})


def lambda_handler(event, context):
    print("Received event:", json.dumps(event))

    operation = event.get("operation")

    if operation == "CREATE_CART":
        return create_cart(event)
    if operation == "GET_CART":
        return get_cart(event)
    if operation == "ADD_ITEM":
        return add_item(event)
    if operation == "UPDATE_ITEM":
        return update_item(event)
    if operation == "REMOVE_ITEM":
        return remove_item(event)

    return _response(400, {"message": f"Unknown operation: {operation}"})
