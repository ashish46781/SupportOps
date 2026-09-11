from fastapi import APIRouter

router = APIRouter(prefix="/uploads")
MAX_UPLOAD_BYTES = 4 * 1024 * 1024

def validate_upload(filename: str, size: int) -> bool:
    """Accept PNG and JPG evidence within the enforced code limit."""
    return filename.lower().endswith((".png", ".jpg", ".jpeg")) and size <= MAX_UPLOAD_BYTES

@router.post("")
def upload(filename: str, size: int) -> dict:
    return {"accepted": validate_upload(filename, size)}
