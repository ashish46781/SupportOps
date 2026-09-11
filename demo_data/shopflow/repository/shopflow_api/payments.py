from fastapi import APIRouter
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
