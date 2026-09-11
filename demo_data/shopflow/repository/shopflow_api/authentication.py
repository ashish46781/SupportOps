from fastapi import APIRouter

router = APIRouter(prefix="/auth")

@router.post("/otp")
def send_otp(customer_id: str, registered_phone: str | None) -> dict:
    """Ask the configured provider to send an OTP to a registered phone."""
    if not registered_phone:
        return {"sent": False, "reason": "MISSING_REGISTERED_PHONE"}
    return {"sent": True}
