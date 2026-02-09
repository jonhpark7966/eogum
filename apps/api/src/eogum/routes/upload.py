from fastapi import APIRouter, Depends

from eogum.auth import get_user_id
from eogum.models.schemas import PresignRequest, PresignResponse
from eogum.services.r2 import generate_presigned_upload

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("/presign", response_model=PresignResponse)
def presign_upload(req: PresignRequest, user_id: str = Depends(get_user_id)):
    upload_url, r2_key = generate_presigned_upload(req.filename, req.content_type)
    return PresignResponse(upload_url=upload_url, r2_key=r2_key)
