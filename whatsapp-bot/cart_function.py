import json
import uuid
from typing import Any, Dict, List


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
        "subTotal": sub_total,
        "discount": int(discount_amount),
        "shipping": int(shipping),
        "tax": int(tax),
        "grandTotal": grand_total,
        "currency": currency,
    }


def _response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to build API Gateway-compatible responses."""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",  # adjust if needed
        },
        "body": json.dumps(body, default=str),
    }


def create_cart(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    operation: CREATE_CART
    event example:
    {
      "operation": "CREATE_CART",
      "userId": "user_123",
      "source": "WHATSAPP"
    }
    """
    user_id = event.get("userId") or "anonymous"
    source = event.get("source") or "UNKNOWN"

    cart_id = f"cart_{uuid.uuid4().hex[:8]}"
    items: List[Dict[str, Any]] = []  # empty at start

    cart = {
        "cartId": cart_id,
        "userId": user_id,
        "source": source,
        "status": "ACTIVE",
        "items": items,
        "couponCode": None,
        "discountReason": None,
        "totals": calculate_totals(items),
    }

    # Later: persist this cart in DynamoDB (ds-carts)
    return _response(201, cart)


def get_cart(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    operation: GET_CART
    event example:
    {
      "operation": "GET_CART",
      "cartId": "cart_abc"
    }
    """
    cart_id = event.get("cartId")
    if not cart_id:
        return _response(400, {"message": "cartId is required"})

    # Mock cart – later fetch from DynamoDB
    items = [
        {
            "itemId": "item_001",
            "productId": "prod_123",
            "name": "Mock Hawan Samagri Kit",
            "quantity": 1,
            "unitPrice": 599,
            "lineTotal": 599,
        }
    ]

    # Example simple discount rule (reuse same logic)
    discount_amount = 0
    sub_total = sum(i["lineTotal"] for i in items)
    if sub_total >= 1000:
        discount_amount = int(sub_total * 0.10)

    cart = {
        "cartId": cart_id,
        "userId": "user_123",
        "status": "ACTIVE",
        "items": items,
        "couponCode": "AUTO10" if discount_amount > 0 else None,
        "discountReason": "10% discount on orders above ₹1000"
        if discount_amount > 0
        else None,
        "totals": calculate_totals(items, discount_amount=discount_amount),
    }

    return _response(200, cart)


def add_item(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    operation: ADD_ITEM
    event example:
    {
      "operation": "ADD_ITEM",
      "cartId": "cart_abc",
      "payload": {
        "productId": "prod_123",
        "quantity": 2,
        "notes": "",
        "customizationType": "STANDARD"
      }
    }
    """
    cart_id = event.get("cartId")
    payload = event.get("payload") or {}

    if not cart_id:
        return _response(400, {"message": "cartId is required"})

    product_id = payload.get("productId")
    quantity = int(payload.get("quantity", 1))

    if not product_id:
        return _response(400, {"message": "productId is required"})

    item_id = f"item_{uuid.uuid4().hex[:8]}"

    # Mock pricing – later fetch from Products table
    unit_price = 599
    line_total = unit_price * quantity

    item = {
        "itemId": item_id,
        "productId": product_id,
        "name": "Mock Product From Catalog",
        "quantity": quantity,
        "unitPrice": unit_price,
        "lineTotal": line_total,
    }

    # For now, pretend cart had only this item
    items = [item]

    # Example simple discount rule
    discount_amount = 0
    sub_total = sum(i["lineTotal"] for i in items)
    if sub_total >= 1000:
        discount_amount = int(sub_total * 0.10)  # 10% off

    cart = {
        "cartId": cart_id,
        "userId": "user_123",
        "status": "ACTIVE",
        "items": items,
        "couponCode": "AUTO10" if discount_amount > 0 else None,
        "discountReason": "10% discount on orders above ₹1000"
        if discount_amount > 0
        else None,
        "totals": calculate_totals(items, discount_amount=discount_amount),
    }

    return _response(200, cart)


def update_item(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    operation: UPDATE_ITEM
    event example:
    {
      "operation": "UPDATE_ITEM",
      "cartId": "cart_abc",
      "itemId": "item_001",
      "payload": { "quantity": 3 }
    }
    """
    cart_id = event.get("cartId")
    item_id = event.get("itemId")
    payload = event.get("payload") or {}

    if not cart_id or not item_id:
        return _response(400, {"message": "cartId and itemId are required"})

    quantity = int(payload.get("quantity", 1))

    # Mock updated item – later: fetch from DynamoDB and update quantity
    item = {
        "itemId": item_id,
        "productId": "prod_123",
        "name": "Mock Hawan Samagri Kit",
        "quantity": quantity,
        "unitPrice": 599,
        "lineTotal": 599 * quantity,
    }

    items = [item]

    # Reapply same discount rule
    discount_amount = 0
    sub_total = sum(i["lineTotal"] for i in items)
    if sub_total >= 1000:
        discount_amount = int(sub_total * 0.10)

    cart = {
        "cartId": cart_id,
        "userId": "user_123",
        "status": "ACTIVE",
        "items": items,
        "couponCode": "AUTO10" if discount_amount > 0 else None,
        "discountReason": "10% discount on orders above ₹1000"
        if discount_amount > 0
        else None,
        "totals": calculate_totals(items, discount_amount=discount_amount),
    }

    return _response(200, cart)


def remove_item(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    operation: REMOVE_ITEM
    event example:
    {
      "operation": "REMOVE_ITEM",
      "cartId": "cart_abc",
      "itemId": "item_001"
    }
    """
    cart_id = event.get("cartId")
    item_id = event.get("itemId")

    if not cart_id or not item_id:
        return _response(400, {"message": "cartId and itemId are required"})

    # Mock: assume item removed and cart now empty
    items: List[Dict[str, Any]] = []

    cart = {
        "cartId": cart_id,
        "userId": "user_123",
        "status": "ACTIVE",
        "items": items,
        "couponCode": None,
        "discountReason": None,
        "totals": calculate_totals(items),
    }

    return _response(200, cart)


def lambda_handler(event, context):
    """
    This Lambda expects the mapped JSON from API Gateway, e.g.:

    {
      "operation": "ADD_ITEM",
      "cartId": "cart_abc",
      "payload": { ... }
    }
    """
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
