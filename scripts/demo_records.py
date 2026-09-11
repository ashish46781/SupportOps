from __future__ import annotations

from datetime import UTC, datetime, timedelta

FIXED_NOW = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)

CUSTOMERS = [
    ("CUST-001", "Ananya Sharma"),
    ("CUST-002", "Rahul Verma"),
    ("CUST-003", "Sara Khan"),
    ("CUST-004", "Vikram Patel"),
    ("CUST-005", "Neha Singh"),
    ("CUST-006", "Arjun Mehta"),
    ("CUST-007", "Mira Iyer"),
    ("CUST-008", "Kabir Das"),
    ("CUST-009", "Tara Bose"),
    ("CUST-010", "Rohan Gupta"),
    ("CUST-011", "Ishita Rao"),
    ("CUST-012", "Dev Nair"),
]

CURRENT = """TKT-1001|CUST-001|ORD-1001|Payment captured but order failed|My payment was captured, but the order shows failed. The checkout screen shows PAYMENT_SYNC_502.|PAYMENT|HIGH|NEW|payment_failed.png|PAYMENT_SYNC_502
TKT-1002|CUST-002|ORD-1002|Refund pending beyond SLA|The refund has remained pending for nine business days.|REFUND|HIGH|IN_PROGRESS|refund_pending.png|REFUND_PENDING_09D
TKT-1003|CUST-003|ORD-1003|OTP not received after phone change|I changed my phone number and the sign-in OTP has not arrived.|LOGIN|MEDIUM|WAITING_FOR_CUSTOMER|otp_error.png|OTP_DELIVERY_401
TKT-1004|CUST-004|ORD-1004|PNG image upload fails|A product return image is rejected even though it is a PNG.|IMAGE_UPLOAD|HIGH|ESCALATED|image_upload_error.png|UPLOAD_SIZE_413
TKT-1005|CUST-005|ORD-1005|Unknown payment on account|I do not recognize a captured payment listed on my account.|SECURITY|CRITICAL|NEW||UNKNOWN_PAYMENT
TKT-1006|CUST-006|ORD-1006|Coupon not applied|SAVE20 was accepted but the total did not change.|COUPON|LOW|NEW||COUPON_RULE_MISMATCH
TKT-1007|CUST-007|ORD-1007|Payment authorization failed|Checkout says payment authorization failed.|PAYMENT|MEDIUM|IN_PROGRESS||PAYMENT_AUTH_402
TKT-1008|CUST-008|ORD-1008|Refund status unclear|The app gives no expected refund date.|REFUND|LOW|NEW||
TKT-1009|CUST-009|ORD-1009|Login challenge loops|The verification screen keeps returning to sign in.|LOGIN|MEDIUM|WAITING_FOR_CUSTOMER||LOGIN_LOOP_409
TKT-1010|CUST-010|ORD-1010|JPG upload rejected|A 3 MB JPG receipt is rejected.|IMAGE_UPLOAD|MEDIUM|NEW||UPLOAD_TYPE_415
TKT-1011|CUST-011|ORD-1011|Expired coupon displayed|The app displayed a coupon that checkout says is expired.|COUPON|LOW|IN_PROGRESS||COUPON_EXPIRED
TKT-1012|CUST-012|ORD-1012|Duplicate payment concern|Two pending entries appear for one order.|SECURITY|HIGH|ESCALATED||DUPLICATE_PAYMENT"""

HISTORICAL = """TKT-0901|CUST-001|ORD-1013|Earlier callback sync failure|Captured payment and failed order after callback error.|PAYMENT|RESOLVED|Callback was replayed and reconcile_order restored the order.
TKT-0902|CUST-002|ORD-1014|Earlier refund delay|Refund waited during worker backlog.|REFUND|CLOSED|Refund worker recovered and the refund completed.
TKT-0903|CUST-003|ORD-1015|Old OTP delay|OTP delayed while roaming.|LOGIN|RESOLVED|Customer confirmed the registered number and delivery resumed.
TKT-0904|CUST-004|ORD-1016|Image was too large|Return PNG exceeded the accepted size.|IMAGE_UPLOAD|CLOSED|Customer compressed the PNG below the enforced limit.
TKT-0905|CUST-005|ORD-1017|Account review completed|Customer requested a security review.|SECURITY|CLOSED|Specialist completed identity-safe account review.
TKT-0906|CUST-006|ORD-1018|SAVE20 eligibility issue|Coupon complaint for excluded product.|COUPON|RESOLVED|Explained documented product exclusion.
TKT-0907|CUST-006|ORD-1019|WELCOME10 not applied|Coupon was outside its date window.|COUPON|CLOSED|Confirmed expiry date without changing the order.
TKT-0908|CUST-007|ORD-1007|Payment retry succeeded|First authorization timed out.|PAYMENT|RESOLVED|Verified no capture before suggesting a retry.
TKT-0909|CUST-008|ORD-1008|Refund requested|Asked how to request a refund.|REFUND|CLOSED|Provided the policy-aligned request steps.
TKT-0910|CUST-009|ORD-1009|OTP destination check|OTP was sent to an old number.|LOGIN|RESOLVED|Customer verified the registered number through the safe process.
TKT-0911|CUST-010|ORD-1010|Receipt upload fixed|PDF receipt upload failed.|IMAGE_UPLOAD|CLOSED|Customer uploaded a supported image format.
TKT-0912|CUST-011|ORD-1011|Coupon minimum not met|Cart was below the minimum.|COUPON|RESOLVED|Explained the minimum order amount."""


def iso(days: int) -> str:
    return (FIXED_NOW + timedelta(days=days)).isoformat()


def records() -> dict:
    customers = [
        {
            "customer_id": customer_id,
            "name": name,
            "email": f"{name.lower().replace(' ', '.')}@example.test",
            "plan": "PLUS" if index % 3 == 0 else "STANDARD",
            "created_at": iso(-500 + index),
        }
        for index, (customer_id, name) in enumerate(CUSTOMERS)
    ]
    amounts = [
        2499,
        1299,
        799,
        3499,
        1899,
        599,
        1599,
        2799,
        999,
        4499,
        699,
        2199,
        899,
        1399,
        3199,
        1099,
        2599,
        749,
        1799,
    ]
    orders = []
    payments = []
    for index, amount in enumerate(amounts, start=1):
        customer_id = f"CUST-{((index - 1) % 12) + 1:03d}"
        orders.append(
            {
                "order_id": f"ORD-{1000 + index}",
                "customer_id": customer_id,
                "amount": amount,
                "status": "FAILED" if index == 1 else ("REFUNDED" if index == 2 else "CONFIRMED"),
                "created_at": iso(-20 + index),
            }
        )
        payments.append(
            {
                "payment_id": f"PAY-{1000 + index}",
                "order_id": f"ORD-{1000 + index}",
                "customer_id": customer_id,
                "amount": amount,
                "status": "CAPTURED"
                if index in {1, 5}
                else ("REFUND_PENDING" if index == 2 else "PAID"),
                "provider_reference": f"DEMO-PROVIDER-{1000 + index}",
                "created_at": iso(-20 + index),
            }
        )
    tickets = []
    for offset, line in enumerate(CURRENT.splitlines()):
        fields = line.split("|")
        ticket_id, customer_id, order_id, subject, description = fields[:5]
        category, priority, status, screenshot, error = fields[5:]
        tickets.append(
            {
                "ticket_id": ticket_id,
                "customer_id": customer_id,
                "order_id": order_id,
                "subject": subject,
                "description": description,
                "category": category,
                "priority": priority,
                "status": status,
                "screenshot_path": f"demo_data/shopflow/screenshots/{screenshot}"
                if screenshot
                else None,
                "error_code": error or None,
                "created_at": iso(-offset),
                "resolved_at": None,
                "resolution": None,
            }
        )
    for offset, line in enumerate(HISTORICAL.splitlines()):
        fields = line.split("|")
        ticket_id, customer_id, order_id, subject, description, category, status, resolution = (
            fields
        )
        tickets.append(
            {
                "ticket_id": ticket_id,
                "customer_id": customer_id,
                "order_id": order_id,
                "subject": subject,
                "description": description,
                "category": category,
                "priority": "MEDIUM",
                "status": status,
                "screenshot_path": None,
                "error_code": None,
                "created_at": iso(-200 - offset),
                "resolved_at": iso(-190 - offset),
                "resolution": resolution,
            }
        )
    return {"customers": customers, "orders": orders, "payments": payments, "tickets": tickets}
