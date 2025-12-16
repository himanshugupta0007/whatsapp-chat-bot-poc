import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

dynamodb = boto3.resource("dynamodb")
PRODUCTS_TABLE = os.environ.get("PRODUCTS_TABLE", "ds-products")
table = dynamodb.Table(PRODUCTS_TABLE)
# Allowed category values (adjust as needed)
ALLOWED_CATEGORIES = ["KIT", "ESSENTIAL"]


class DecimalEncoder(json.JSONEncoder):
    """Convert DynamoDB Decimal types to int/float in JSON response."""

    def default(self, o):
        if isinstance(o, Decimal):
            if o % 1 == 0:
                return int(o)
            return float(o)
        return super().default(o)


def build_response(status_code: int, body: dict):
    return {
        "statusCode": status_code,
        "body": body
    }


# ---------- CREATE PRODUCT ----------

def create_product(event):
    """
    Expected payload:
    {
      "operation": "CREATE_PRODUCT",
      "product": {
        "name": "Ghee Diya Batti",
        "price": 199,
        "category": "puja",
        "description": "...",
        // optional:
        "productId": "prod_custom_001",
        "sortKey": "PUJA#2025-12-05T10:00:00Z"
      }
    }
    """

    if event.get("product") is None or event.get("product") == {}:
        return build_response(
            400,
            {
                "message": "Missing required fields: product",
            },
        )

    product = event.get("product") or {}

    name = product.get("name")
    price = product.get("price")
    category = product.get("category")

    if name is None or price is None or category is None:
        return build_response(
            400,
            {
                "message": "Missing required fields: name, price, category",
            },
        )

    # Normalize and validate category
    if isinstance(category, str):
        category_norm = category.strip().upper()
    else:
        category_norm = None

    if not category_norm or category_norm not in [c.upper() for c in ALLOWED_CATEGORIES]:
        return build_response(
            400,
            {"message": f"Invalid category. Allowed values: {ALLOWED_CATEGORIES}"},
        )

    # Use canonical form from the allowed list
    for c in ALLOWED_CATEGORIES:
        if c.upper() == category_norm:
            category = c
            break

    currency = product.get("currency") or "INR"
    type = product.get("type")
    description = product.get("description")
    images = product.get("images") or []
    tags = product.get("tags") or []

    if images is None:
        images = []
    elif isinstance(images, list):
        # already a list, keep as-is
        pass
    else:
        # fallback to empty list for unsupported types
        images = []

    if tags is None:
        tags = []
    elif not isinstance(tags, list):
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        else:
            # fallback to empty list for unsupported types
            tags = []

    if not name or price is None or not category:
        return build_response(
            400,
            {
                "message": "Missing required fields: name, price, category",
            },
        )

    now = datetime.now(timezone.utc).isoformat()

    product_id = product.get("sku") or f"prod_{uuid.uuid4().hex[:12]}"
    sort_key = product.get("sortKey") or f"{category.upper()}#{type.upper()}"

    item = {
        "productId": product_id,
        "name": name,
        "price": price,
        "category": category,
        "sortKey": sort_key,
        "createdAt": now,
        "updatedAt": now,
        "isActive": True,
        "tags": tags,
        "currency": currency,
        "description": description,
        "images": images,
        "type": type,
    }

    # Merge any additional fields from product (except productId/sortKey, which we overwrote)
    for key, value in product.items():
        if key not in ["productId", "sortKey", "name", "price", "category"]:
            item[key] = value

    try:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(productId)",  # avoid overwrite
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return build_response(
                409,  # Conflict
                {"message": "Product with this productId already exists"},
            )
        print("[CREATE_PRODUCT] DynamoDB error:", e)
        return build_response(500, {"message": "Internal server error"})

    return build_response(201, item)


# ---------- GET PRODUCT (BY ID) ----------

def get_product(event):
    """
    Expected:
    {
      "operation": "GET_PRODUCT",
      "id": "prod_123"
    }
    """
    product_id = event.get("id")
    print(f"[GET_PRODUCT] id={product_id}")

    if not product_id:
        return build_response(
            400,
            {"message": "Path parameter 'id' is required"},
        )

    try:
        resp = table.get_item(Key={"productId": product_id})
    except Exception as e:
        print(f"[GET_PRODUCT] DynamoDB error: {e}")
        return build_response(500, {"message": "Internal server error"})

    item = resp.get("Item")
    if not item:
        return build_response(404, {"message": "Product not found"})

    return build_response(200, item)


# ---------- LIST PRODUCTS (simple scan with pagination) ----------

def list_products(event):
    filters = event.get("filters", {}) or {}
    pagination = event.get("pagination", {}) or {}

    # Page size
    limit = int(pagination.get("limit", 20))
    limit = max(1, min(limit, 100))

    # Cursor-based pagination
    next_token = pagination.get("nextToken")

    category = filters.get("category")
    search = filters.get("search")

    print(f"[LIST_PRODUCTS] filters={filters}, limit={limit}, nextToken={next_token}")

    scan_kwargs = {
        "Limit": limit,
        # Optional: only fetch required attributes to reduce RCU & network
        # "ProjectionExpression": "productId, #n, price, category",
        # "ExpressionAttributeNames": {"#n": "name"},
    }

    # Build filter expression (still post-read, but at least only per page)
    filter_expression = None

    if category:
        filter_expression = Attr("category").eq(category)

    if search:
        # NOTE: this still forces scan; better to move to a GSI / external search later
        search_expr = (
                Attr("name").contains(search) | Attr("description").contains(search)
        )
        filter_expression = (
            search_expr if filter_expression is None else (filter_expression & search_expr)
        )

    if filter_expression is not None:
        scan_kwargs["FilterExpression"] = filter_expression

    # Resume from last key if provided
    if next_token:
        try:
            scan_kwargs["ExclusiveStartKey"] = json.loads(next_token)
        except json.JSONDecodeError:
            print("[LIST_PRODUCTS] Invalid nextToken, ignoring")

    resp = table.scan(**scan_kwargs)

    items = resp.get("Items", [])
    last_evaluated_key = resp.get("LastEvaluatedKey")
    new_next_token = json.dumps(last_evaluated_key) if last_evaluated_key else None

    print(f"[LIST_PRODUCTS] returned={len(items)}, hasMore={bool(last_evaluated_key)}")

    return build_response(
        200,
        {
            "items": items,
            "limit": limit,
            "nextToken": new_next_token,
            # You can drop `total` or maintain it separately in a cheap way (counter table)
        },
    )


# ---------- UPDATE PRODUCT ----------

def update_product(event):
    """
    Expected:
    {
      "operation": "UPDATE_PRODUCT",
      "id": "prod_123",
      "updates": {
        "name": "New Name",
        "price": 249,
        "category": "new-category",   // if changed, we also keep old sortKey or regenerate
        "description": "Updated..."
      }
    }
    """
    product_id = event.get("id")
    updates = event.get("updates") or {}

    if not product_id:
        return build_response(400, {"message": "Path parameter 'id' is required"})

    if not updates:
        return build_response(400, {"message": "No updates provided"})

    # Do not allow productId to be changed
    if "productId" in updates:
        updates.pop("productId")

    now = datetime.now(timezone.utc).isoformat()
    updates["updatedAt"] = now

    # If category is updated but no sortKey given, optionally adjust sortKey
    if "category" in updates and "sortKey" not in updates:
        updates["sortKey"] = f"{updates['category'].upper()}#{now}"

    # Build UpdateExpression
    update_expr_parts = []
    expr_attr_names = {}
    expr_attr_values = {}

    for i, (key, value) in enumerate(updates.items()):
        placeholder_name = f"#f{i}"
        placeholder_value = f":v{i}"
        update_expr_parts.append(f"{placeholder_name} = {placeholder_value}")
        expr_attr_names[placeholder_name] = key
        expr_attr_values[placeholder_value] = value

    update_expression = "SET " + ", ".join(update_expr_parts)

    try:
        resp = table.update_item(
            Key={"productId": product_id},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values,
            ConditionExpression="attribute_exists(productId)",
            ReturnValues="ALL_NEW",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return build_response(404, {"message": "Product not found"})
        print("[UPDATE_PRODUCT] DynamoDB error:", e)
        return build_response(500, {"message": "Internal server error"})

    updated_item = resp.get("Attributes", {})
    return build_response(200, updated_item)


# ---------- DELETE PRODUCT ----------

def delete_product(event):
    """
    Expected:
    {
      "operation": "DELETE_PRODUCT",
      "id": "prod_123"
    }
    """
    product_id = event.get("id")

    if not product_id:
        return build_response(400, {"message": "Path parameter 'id' is required"})

    try:
        resp = table.delete_item(
            Key={"productId": product_id},
            ConditionExpression="attribute_exists(productId)",
            ReturnValues="ALL_OLD",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return build_response(404, {"message": "Product not found"})
        print("[DELETE_PRODUCT] DynamoDB error:", e)
        return build_response(500, {"message": "Internal server error"})

    deleted_item = resp.get("Attributes", {})
    return build_response(200, {"message": "Product deleted", "item": deleted_item})


# ---------- MAIN HANDLER ----------

def lambda_handler(event, context):
    print("Received event:", json.dumps(event))

    operation = event.get("operation")

    if operation == "CREATE_PRODUCT":
        return create_product(event)

    elif operation == "LIST_PRODUCTS":
        return list_products(event)

    elif operation == "GET_PRODUCT":
        return get_product(event)

    elif operation == "UPDATE_PRODUCT":
        return update_product(event)

    elif operation == "DELETE_PRODUCT":
        return delete_product(event)

    else:
        return build_response(400, {"message": "Unknown operation"})
