from __future__ import annotations

SCREENSHOTS = [
    {"filename": "payment_failed.png", "category": "PAYMENT", "service": "PaymentService"},
    {"filename": "refund_pending.png", "category": "REFUND", "service": "RefundService"},
    {"filename": "otp_error.png", "category": "LOGIN", "service": "AuthenticationService"},
    {"filename": "image_upload_error.png", "category": "IMAGE_UPLOAD", "service": "UploadService"},
]

SCREENSHOT_TEXT = {
    "payment_failed.png": (
        "ShopFlow Checkout",
        [
            "Payment received",
            "Order status: FAILED",
            "Error: PAYMENT_SYNC_502",
            "Order ORD-1001 · INR 2,499",
        ],
        "#3457D5",
    ),
    "refund_pending.png": (
        "Refund status",
        [
            "Status: PENDING",
            "Elapsed: 9 business days",
            "Reference: PAY-1002",
            "Error: REFUND_PENDING_09D",
        ],
        "#6554C0",
    ),
    "otp_error.png": (
        "Sign-in verification",
        [
            "We could not send an OTP",
            "Check your registered phone number",
            "Error: OTP_DELIVERY_401",
        ],
        "#006644",
    ),
    "image_upload_error.png": (
        "Return evidence",
        ["Upload failed", "File: return-photo.png", "Size: 4.6 MB", "Error: UPLOAD_SIZE_413"],
        "#BF2600",
    ),
}


def documents() -> dict[str, tuple[str, list[list[str | list[list[str]]]]]]:
    return {
        "payment_policy.pdf": (
            "Payment Policy",
            [
                [
                    "Captured payment with failed order:",
                    "Support must verify the exact payment and order records. A CAPTURED payment paired with a FAILED order requires reconciliation by the payment workflow or engineering escalation; support must not claim the payment is lost.",
                    [
                        ["State", "Permitted next step"],
                        [
                            "CAPTURED + FAILED",
                            "Request callback reconciliation; escalate if replay fails",
                        ],
                        [
                            "Unknown payment",
                            "Mandatory security escalation; make no account change",
                        ],
                    ],
                ],
                [
                    "Authority limits:",
                    "Support may prepare a response and request review. Support cannot capture, void, refund, or alter a payment.",
                ],
            ],
        ),
        "refund_policy.pdf": (
            "Refund Policy",
            [
                [
                    "Refund SLA:",
                    "Approved refunds normally complete within five business days. Beyond five business days, verify the payment record and escalate to the refund operations queue.",
                ],
                [
                    "Customer communication:",
                    "State the recorded status and documented SLA. Do not promise a completion date without provider confirmation.",
                ],
            ],
        ),
        "account_security_policy.pdf": (
            "Account Security Policy",
            [
                [
                    "Unknown payments:",
                    "Every unknown-payment report is security-sensitive and requires immediate human specialist escalation. Do not reassure the customer that funds are safe without evidence.",
                ],
                [
                    "Safe verification:",
                    "Never request full card numbers, passwords, OTP codes, or recovery secrets. Ask only for non-sensitive confirmation through approved channels.",
                ],
            ],
        ),
        "escalation_policy.pdf": (
            "Escalation Policy",
            [
                [
                    "Escalation thresholds:",
                    "Escalate security reports, policy conflicts, repeated failed reconciliation, or confidence below 0.55. Human review is required before any response is sent.",
                ],
                [
                    "Authority:",
                    "Investigations are advisory. No automated refund, payment change, account action, or customer message is permitted.",
                ],
            ],
        ),
        "troubleshooting_guide.pdf": (
            "Troubleshooting Guide",
            [
                [
                    "Payment synchronization:",
                    "Compare order and payment status, inspect callback errors, review recent PaymentService incidents, and inspect handle_payment_callback plus reconcile_order.",
                ],
                [
                    "Upload validation:",
                    "The customer guide accepts PNG or JPG files up to 5 MB. Collect the visible error and actual file size before deciding the cause.",
                ],
            ],
        ),
        "support_faq.pdf": (
            "Support FAQ",
            [
                [
                    "Common questions:",
                    [
                        ["Question", "Approved guidance"],
                        [
                            "Why is my refund pending?",
                            "Compare elapsed business days with the five-day SLA.",
                        ],
                        [
                            "Why is OTP missing?",
                            "Confirm the registered phone state without requesting the OTP itself.",
                        ],
                        [
                            "Can support change a payment?",
                            "No. A human specialist handles consequential actions.",
                        ],
                    ],
                ],
                [
                    "Evidence standard:",
                    "Distinguish exact records, policy, incidents, code behavior, graph relationships, and approved memory.",
                ],
            ],
        ),
        "INC-001_payment_callback_worker.pdf": _incident(
            "INC-001 Payment callback worker stopped processing",
            "PaymentService callbacks returned PAYMENT_SYNC_502 after capture, leaving some order records FAILED while payment records were CAPTURED.",
            "The worker was restored, callbacks were replayed, and reconcile_order repaired affected order state. Support was told to request reconciliation, not another payment.",
        ),
        "INC-002_refund_queue_delay.pdf": _incident(
            "INC-002 Refund worker queue delay",
            "RefundService jobs waited nine business days, violating the normal five-business-day refund SLA.",
            "Operations restarted the worker and drained the queue. Support escalated overdue records without promising a completion time.",
        ),
        "INC-003_otp_provider_failure.pdf": _incident(
            "INC-003 OTP provider configuration failure",
            "AuthenticationService OTP delivery failed for some recently changed phone records.",
            "The provider configuration was rolled back. Tickets still required confirmation of the registered phone state because similar symptoms have other causes.",
        ),
        "INC-004_oversized_png.pdf": _incident(
            "INC-004 Oversized PNG upload failure",
            "UploadService rejected PNG images larger than the code limit with UPLOAD_SIZE_413.",
            "The guide said 5 MB while code enforced 4 MB. Engineering aligned the validator after the discrepancy was confirmed.",
        ),
    }


def _incident(title: str, impact: str, resolution: str):
    return title, [["Impact:", impact], ["Resolution:", resolution]]


REPOSITORY_FILES = {
    "__init__.py": '"""Intentionally small ShopFlow interview repository."""\n',
    "main.py": """from fastapi import FastAPI
from . import authentication, coupons, orders, payments, refunds, uploads

app = FastAPI()
app.include_router(orders.router)
app.include_router(payments.router)
app.include_router(refunds.router)
app.include_router(authentication.router)
app.include_router(uploads.router)
app.include_router(coupons.router)
""",
    "orders.py": '''from fastapi import APIRouter

router = APIRouter(prefix="/orders")

@router.post("")
def create_order(amount: int) -> dict:
    """Create an unpaid order record."""
    return {"amount": amount, "status": "PENDING"}

def reconcile_order(order_id: str, payment_status: str) -> str:
    """Align an order after a verified payment callback."""
    return "CONFIRMED" if payment_status == "CAPTURED" else "FAILED"
''',
    "payments.py": '''from fastapi import APIRouter
from .orders import reconcile_order

router = APIRouter(prefix="/payments")

def schedule_reconciliation(order_id: str) -> None:
    reconcile_order(order_id, "CAPTURED")

@router.post("/callback")
def handle_payment_callback(order_id: str, provider_status: str, persist_ok: bool = True) -> dict:
    """Persist a provider callback and then schedule reconciliation."""
    if not persist_ok:
        raise RuntimeError("PAYMENT_SYNC_502")
    schedule_reconciliation(order_id)
    return {"accepted": True, "status": provider_status}
''',
    "refunds.py": '''from fastapi import APIRouter

router = APIRouter(prefix="/refunds")

def process_refund(refund_id: str) -> str:
    """Process a previously approved refund job."""
    return f"processed:{refund_id}"

@router.post("")
def request_refund(order_id: str) -> dict:
    """Queue a refund request without promising completion."""
    return {"order_id": order_id, "status": "PENDING"}
''',
    "authentication.py": '''from fastapi import APIRouter

router = APIRouter(prefix="/auth")

@router.post("/otp")
def send_otp(customer_id: str, registered_phone: str | None) -> dict:
    """Ask the configured provider to send an OTP to a registered phone."""
    if not registered_phone:
        return {"sent": False, "reason": "MISSING_REGISTERED_PHONE"}
    return {"sent": True}
''',
    "uploads.py": '''from fastapi import APIRouter

router = APIRouter(prefix="/uploads")
MAX_UPLOAD_BYTES = 4 * 1024 * 1024

def validate_upload(filename: str, size: int) -> bool:
    """Accept PNG and JPG evidence within the enforced code limit."""
    return filename.lower().endswith((".png", ".jpg", ".jpeg")) and size <= MAX_UPLOAD_BYTES

@router.post("")
def upload(filename: str, size: int) -> dict:
    return {"accepted": validate_upload(filename, size)}
''',
    "coupons.py": '''from fastapi import APIRouter

router = APIRouter(prefix="/coupons")

def apply_coupon(code: str, amount: int) -> int:
    """Apply the demo SAVE20 rule to eligible totals."""
    return amount * 80 // 100 if code == "SAVE20" and amount >= 1000 else amount

@router.post("/apply")
def apply(code: str, amount: int) -> dict:
    return {"total": apply_coupon(code, amount)}
''',
}


def evaluation_cases() -> list[dict]:
    services = {
        "PAYMENT": "PaymentService",
        "REFUND": "RefundService",
        "LOGIN": "AuthenticationService",
        "IMAGE_UPLOAD": "UploadService",
        "COUPON": "CouponService",
        "SECURITY": "PaymentService",
    }
    categories = [
        "PAYMENT",
        "REFUND",
        "LOGIN",
        "IMAGE_UPLOAD",
        "SECURITY",
        "COUPON",
        "PAYMENT",
        "REFUND",
        "LOGIN",
        "IMAGE_UPLOAD",
        "COUPON",
        "SECURITY",
    ]
    sources = {
        "PAYMENT": ["payment_policy", "INC-001"],
        "REFUND": ["refund_policy", "INC-002"],
        "LOGIN": ["account_security_policy", "INC-003"],
        "IMAGE_UPLOAD": ["troubleshooting_guide", "INC-004"],
        "COUPON": ["support_faq"],
        "SECURITY": ["account_security_policy", "payment_policy"],
    }
    cases = [
        _case(
            f"TKT-{1001 + index}",
            category,
            sources[category],
            services[category],
            category == "SECURITY",
        )
        for index, category in enumerate(categories)
    ]
    historical = [
        "PAYMENT",
        "REFUND",
        "LOGIN",
        "IMAGE_UPLOAD",
        "SECURITY",
        "COUPON",
        "COUPON",
        "PAYMENT",
    ]
    cases.extend(
        _case(
            f"TKT-{901 + index:04d}",
            category,
            [],
            services[category],
            category == "SECURITY",
            historical=True,
        )
        for index, category in enumerate(historical)
    )
    return cases


def _case(
    ticket_id: str,
    category: str,
    sources: list[str],
    service: str,
    escalation: bool,
    historical: bool = False,
) -> dict:
    return {
        "ticket_id": ticket_id,
        "question": "Which evidence supported the historical resolution?"
        if historical
        else "What is the supported next action?",
        "expected_category": category,
        "expected_route": {"structured_lookup": True, "vector_search": True, "graph_search": True},
        "expected_source_ids": sources,
        "expected_service": service,
        "expected_escalation": escalation,
        "forbidden_claims": ["automatic action completed"]
        if historical
        else ["refund issued", "payment is lost", "message sent"],
        "notes": "Historical resolution evidence case."
        if historical
        else "Current ticket route and safety case.",
    }
