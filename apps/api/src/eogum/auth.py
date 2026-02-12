import logging

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from eogum.config import settings

logger = logging.getLogger(__name__)
security = HTTPBearer()

_jwks_client = PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json")


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Verify Supabase JWT and return decoded payload."""
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(credentials.credentials)
        payload = jwt.decode(
            credentials.credentials,
            signing_key.key,
            algorithms=["ES256"],
            audience="authenticated",
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError as e:
        logger.error("JWT verification failed: %s", e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def get_user_id(token: dict = Depends(verify_token)) -> str:
    """Extract user_id from verified JWT."""
    user_id = token.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: no sub")
    return user_id
