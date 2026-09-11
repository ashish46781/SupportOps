from fastapi import APIRouter

router = APIRouter(prefix="/orders")

@router.post("")
def create_order(amount: int) -> dict:
    """Create an unpaid order record."""
    return {"amount": amount, "status": "PENDING"}

def reconcile_order(order_id: str, payment_status: str) -> str:
    """Align an order after a verified payment callback."""
    return "CONFIRMED" if payment_status == "CAPTURED" else "FAILED"
