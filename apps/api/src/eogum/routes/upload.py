import math
import uuid

from fastapi import APIRouter, Depends

from eogum.auth import get_user_id
from eogum.models.schemas import (
    MultipartCompleteRequest,
    MultipartInitiateRequest,
    MultipartInitiateResponse,
    MultipartPartUrl,
    PresignRequest,
    PresignResponse,
)
from eogum.services.r2 import (
    complete_multipart_upload,
    create_multipart_upload,
    generate_presigned_upload,
    generate_presigned_upload_part,
)

router = APIRouter(prefix="/upload", tags=["upload"])

PART_SIZE = 100 * 1024 * 1024  # 100 MB


@router.post("/presign", response_model=PresignResponse)
def presign_upload(req: PresignRequest, user_id: str = Depends(get_user_id)):
    upload_url, r2_key = generate_presigned_upload(req.filename, req.content_type)
    return PresignResponse(upload_url=upload_url, r2_key=r2_key)


@router.post("/multipart/initiate", response_model=MultipartInitiateResponse)
def initiate_multipart(req: MultipartInitiateRequest, user_id: str = Depends(get_user_id)):
    ext = req.filename.rsplit(".", 1)[-1] if "." in req.filename else ""
    r2_key = f"sources/{uuid.uuid4()}.{ext}" if ext else f"sources/{uuid.uuid4()}"

    upload_id = create_multipart_upload(r2_key, req.content_type)

    num_parts = max(1, math.ceil(req.size_bytes / PART_SIZE))
    part_urls = [
        MultipartPartUrl(
            part_number=i,
            upload_url=generate_presigned_upload_part(r2_key, upload_id, i),
        )
        for i in range(1, num_parts + 1)
    ]

    return MultipartInitiateResponse(
        upload_id=upload_id,
        r2_key=r2_key,
        part_size=PART_SIZE,
        part_urls=part_urls,
    )


@router.post("/multipart/complete")
def complete_multipart(req: MultipartCompleteRequest, user_id: str = Depends(get_user_id)):
    parts = [{"PartNumber": p.part_number, "ETag": p.etag} for p in req.parts]
    complete_multipart_upload(req.r2_key, req.upload_id, parts)
    return {"r2_key": req.r2_key}
