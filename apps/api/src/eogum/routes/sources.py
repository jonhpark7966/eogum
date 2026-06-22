import logging

from fastapi import APIRouter, Depends

from eogum.auth import get_user_id
from eogum.models.schemas import SourceLookupRequest, SourceLookupResponse
from eogum.services.database import get_db
from eogum.services.r2 import object_exists
from eogum.services.source_cache import delete_source_asset, lookup_source_asset, touch_source_asset

router = APIRouter(prefix="/sources", tags=["sources"])
logger = logging.getLogger(__name__)


@router.post("/lookup", response_model=SourceLookupResponse)
def lookup_source(req: SourceLookupRequest, user_id: str = Depends(get_user_id)):
    del user_id
    db = get_db()
    asset = lookup_source_asset(db, sha256=req.sha256, size_bytes=req.size_bytes)
    if not asset:
        return SourceLookupResponse(hit=False)

    r2_key = asset.get("r2_key")
    if not r2_key or not object_exists(r2_key):
        delete_source_asset(db, asset_id=asset["id"])
        logger.warning("Deleted stale source asset cache entry %s for missing R2 key %s", asset["id"], r2_key)
        return SourceLookupResponse(hit=False)

    touch_source_asset(db, asset_id=asset["id"])
    return SourceLookupResponse(
        hit=True,
        r2_key=r2_key,
        source_asset_id=asset.get("id"),
    )
