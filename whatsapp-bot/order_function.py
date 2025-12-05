import json
import uuid
from datetime import datetime
from typing import Any, Dict, List

import common_utility


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


# --------------------------
# Handlers
# --------------------------


def create_order(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    operation: CREATE_ORDER
    event:
    {
      "operation": "CREATE_ORDER",
      "payload": {
        "cartId": "cart_ab12cd34",
        "userId": "user_123",
        "whatsappNumber": "+9198xxxxxx",
        "shippingAddress": { ... },
        "payment": {
           "method": "RAZORPAY" | "COD",
           "returnUrl": "https://..."
        },
        "metadata": { ... }
      }
    }
    """
    payload = event.get("payload") or {}

    cart_id = payload.get("cartId")
    user_id = payload.get("userId")
    whatsapp_number = payload.get("whatsappNumber")
    shipping_address = payload.get("shippingAddress") or {}
    payment_req = payload.get("payment") or {}
    metadata = payload.get("metadata") or {}

    if not cart_id:
        return common_utility.response(400, {"message": "cartId is required"})
    if not user_id and not whatsapp_number:
        return common_utility.response(400, {"message": "Either userId or whatsappNumber is required"})

    # Mock: create a single fake item – later you will load items+totals from ds-carts
    items: List[Dict[str, Any]] = [
        {
            "productId": "prod_123",
            "name": "Mock Hawan Samagri Kit",
            "quantity": 2,
            "unitPrice": 599,
            "lineTotal": 2 * 599,
        }
    ]

    # Simple discount example: 10% if subTotal >= 1000
    discount_amount = 0
    sub_total = sum(i["lineTotal"] for i in items)
    if sub_total >= 1000:
        discount_amount = int(sub_total * 0.10)

    shipping = 80  # mock shipping
    tax = 0  # for now

    totals = calculate_totals(items, discount_amount=discount_amount, shipping=shipping, tax=tax)

    order_id = f"ord_{uuid.uuid4().hex[:10]}"
    order_number = f"DS-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
    now = common_utility.now_iso()

    method = (payment_req.get("method") or "RAZORPAY").upper()

    # Mock payment object
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
        "totals": totals,
        "shippingAddress": shipping_address,
        "payment": payment,
        "couponCode": "AUTO10" if discount_amount > 0 else None,
        "discountReason": "10% discount on orders above ₹1000" if discount_amount > 0 else None,
        "metadata": metadata,
        "createdAt": now,
        "updatedAt": now,
    }

    # Later: put this order into ds-orders and update UserOrdersIndex
    return common_utility.response(201, order)


def get_order(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    operation: GET_ORDER
    {
      "operation": "GET_ORDER",
      "orderId": "ord_001"
    }
    """
    order_id = event.get("orderId")
    if not order_id:
        return common_utility.response(400, {"message": "orderId is required"})

    # Mock items
    items = [
        {
            "productId": "prod_123",
            "name": "Mock Hawan Samagri Kit",
            "quantity": 1,
            "unitPrice": 599,
            "lineTotal": 599,
        }
    ]

    totals = calculate_totals(items, discount_amount=0, shipping=80, tax=0)

    order = {
        "orderId": order_id,
        "orderNumber": "DS-20251203-ABCD",
        "cartId": "cart_mock123",
        "userId": "user_123",
        "whatsappNumber": "+919811112222",
        "status": "PAID",
        "items": items,
        "totals": totals,
        "shippingAddress": {
            "name": "Mock User",
            "phone": "+919811112222",
            "line1": "Mock Address Line 1",
            "line2": "",
            "city": "Delhi",
            "state": "Delhi",
            "pincode": "110085",
            "country": "IN",
        },
        "payment": {
            "method": "RAZORPAY",
            "status": "SUCCESS",
            "razorpayOrderId": "order_mock123",
            "razorpayPaymentId": "pay_mock123",
            "amount": totals["grandTotal"],
            "currency": totals["currency"],
        },
        "couponCode": None,
        "discountReason": None,
        "metadata": {
            "source": "WHATSAPP"
        },
        "createdAt": "2025-12-03T10:00:00Z",
        "updatedAt": "2025-12-03T10:10:00Z",
    }

    return common_utility.response(200, order)


def list_orders(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    operation: LIST_ORDERS
    {
      "operation": "LIST_ORDERS",
      "filters": {
        "userId": "user_123",
        "whatsappNumber": "",
        "status": ""
      },
      "pagination": {
        "page": "1",
        "limit": "10"
      }
    }
    """
    filters = event.get("filters") or {}
    pagination = event.get("pagination") or {}

    user_id = filters.get("userId") or None
    whatsapp = filters.get("whatsappNumber") or None
    status_filter = filters.get("status") or None

    page = int(pagination.get("page", "1"))
    limit = int(pagination.get("limit", "10"))

    # Mock list of two orders
    items_for_order1 = [
        {
            "productId": "prod_123",
            "name": "Mock Hawan Samagri Kit",
            "quantity": 1,
            "unitPrice": 599,
            "lineTotal": 599,
        }
    ]
    items_for_order2 = [
        {
            "productId": "prod_456",
            "name": "Mock Ghee Diya Batti",
            "quantity": 2,
            "unitPrice": 199,
            "lineTotal": 398,
        }
    ]

    totals1 = calculate_totals(items_for_order1, discount_amount=0, shipping=80)
    totals2 = calculate_totals(items_for_order2, discount_amount=0, shipping=80)

    orders = [
        {
            "orderId": "ord_mock_1",
            "orderNumber": "DS-20251203-0001",
            "cartId": "cart_mock_1",
            "userId": user_id or "user_123",
            "whatsappNumber": whatsapp or "+919811112222",
            "status": "DELIVERED",
            "items": items_for_order1,
            "totals": totals1,
            "createdAt": "2025-12-01T10:00:00Z",
            "updatedAt": "2025-12-02T10:00:00Z",
        },
        {
            "orderId": "ord_mock_2",
            "orderNumber": "DS-20251203-0002",
            "cartId": "cart_mock_2",
            "userId": user_id or "user_123",
            "whatsappNumber": whatsapp or "+919811112222",
            "status": "PAID",
            "items": items_for_order2,
            "totals": totals2,
            "createdAt": "2025-12-02T11:00:00Z",
            "updatedAt": "2025-12-02T12:00:00Z",
        },
    ]

    # Ignore status filter & pagination in mock; keep shape ready
    response = {
        "items": orders,
        "page": page,
        "limit": limit,
        "total": len(orders),
    }

    return common_utility.response(200, response)


def update_order_status(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    operation: UPDATE_ORDER_STATUS
    {
      "operation": "UPDATE_ORDER_STATUS",
      "orderId": "ord_001",
      "payload": {
        "status": "PAID",
        "payment": {
          "status": "SUCCESS",
          "razorpayPaymentId": "pay_xxx"
        }
      }
    }
    """
    order_id = event.get("orderId")
    payload = event.get("payload") or {}

    if not order_id:
        return common_utility.response(400, {"message": "orderId is required"})

    new_status = payload.get("status") or "PAID"
    payment_update = payload.get("payment") or {}

    # Mock original order
    items = [
        {
            "productId": "prod_123",
            "name": "Mock Hawan Samagri Kit",
            "quantity": 1,
            "unitPrice": 599,
            "lineTotal": 599,
        }
    ]
    totals = calculate_totals(items, discount_amount=0, shipping=80)

    payment = {
        "method": "RAZORPAY",
        "status": payment_update.get("status", "SUCCESS"),
        "razorpayOrderId": "order_mock123",
        "razorpayPaymentId": payment_update.get("razorpayPaymentId", "pay_mock123"),
        "amount": totals["grandTotal"],
        "currency": totals["currency"],
    }

    order = {
        "orderId": order_id,
        "orderNumber": "DS-20251203-ABCD",
        "cartId": "cart_mock123",
        "userId": "user_123",
        "whatsappNumber": "+919811112222",
        "status": new_status,
        "items": items,
        "totals": totals,
        "shippingAddress": {
            "name": "Mock User",
            "phone": "+919811112222",
            "line1": "Mock Address Line 1",
            "line2": "",
            "city": "Delhi",
            "state": "Delhi",
            "pincode": "110085",
            "country": "IN",
        },
        "payment": payment,
        "couponCode": None,
        "discountReason": None,
        "metadata": {
            "source": "WHATSAPP"
        },
        "createdAt": "2025-12-03T10:00:00Z",
        "updatedAt": common_utility.now_iso(),
    }

    # later: update ds-orders with new status/payment
    return common_utility.response(200, order)


# --------------------------
# Lambda entrypoint
# --------------------------


def lambda_handler(event, context):
    """
    Expected shapes (via VTL):

    POST /orders:
      { "operation": "CREATE_ORDER", "payload": { ... } }

    GET /orders/{orderId}:
      { "operation": "GET_ORDER", "orderId": "ord_001" }

    GET /orders?userId=...:
      {
        "operation": "LIST_ORDERS",
        "filters": { ... },
        "pagination": { ... }
      }

    PATCH /orders/{orderId}/status:
      {
        "operation": "UPDATE_ORDER_STATUS",
        "orderId": "ord_001",
        "payload": { ... }
      }
    """
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

    return common_utility.response(400, {"message": f"Unknown operation: {op}"})
