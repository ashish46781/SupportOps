from fastapi import APIRouter

router = APIRouter(prefix="/coupons")

def apply_coupon(code: str, amount: int) -> int:
    """Apply the demo SAVE20 rule to eligible totals."""
    return amount * 80 // 100 if code == "SAVE20" and amount >= 1000 else amount

@router.post("/apply")
def apply(code: str, amount: int) -> dict:
    return {"total": apply_coupon(code, amount)}
