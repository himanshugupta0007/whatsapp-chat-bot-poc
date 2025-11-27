import json


def lambda_handler(event, context):
    print("Received event:", json.dumps(event))  # always log input

    operation = event.get("operation")

    # LIST PRODUCTS
    if operation == "LIST_PRODUCTS":
        filters = event.get("filters", {})
        pagination = event.get("pagination", {})

        page = int(pagination.get("page", "1"))
        limit = int(pagination.get("limit", "20"))

        category = filters.get("category")
        search = filters.get("search")

        print("Listing products with filters:", filters, "and pagination:", pagination)

        # Minimal mock response
        return {
            "statusCode": 200,
            "body": json.dumps({
                "items": [
                    {"id": "prod_001", "name": "Mock Product A", "price": 100},
                    {"id": "prod_002", "name": "Mock Product B", "price": 200}
                ],
                "total": 2,
                "page": page,
                "limit": limit
            })
        }

    # GET PRODUCT BY ID
    elif operation == "GET_PRODUCT":
        product_id = event.get("id")
        print("Getting product with ID:", product_id)

        if not product_id:
            return {
                "statusCode": 400,
                "body": json.dumps({"message": "Path parameter 'id' is required"})
            }

        # Minimal mock response
        return {
            "statusCode": 200,
            "body": json.dumps({
                "id": product_id,
                "name": "Mock Product Detail",
                "price": 150
            })
        }

    # UNKNOWN OPERATION
    else:
        return {
            "statusCode": 400,
            "body": json.dumps({"message": "Unknown operation"})
        }
