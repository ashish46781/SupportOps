from fastapi import APIRouter

router = APIRouter(prefix="/refunds")

def process_refund(refund_id: str) -> str:
    """Process a previously approved refund job."""
    return f"processed:{refund_id}"

@router.post("")
def request_refund(order_id: str) -> dict:
    """Queue a refund request without promising completion."""
    return {"order_id": order_id, "status": "PENDING"}
