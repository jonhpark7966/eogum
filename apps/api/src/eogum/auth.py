import logging
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient, PyJWKClientError

from eogum.config import settings

logger = logging.getLogger(__name__)
security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)

_jwks_client = PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json")


def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Verify Supabase JWT and return decoded payload."""
    return _decode_token(credentials.credentials)


def verify_optional_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
) -> dict | None:
    """Verify Supabase JWT when present, otherwise allow anonymous requests."""
    if credentials is None:
        return None
    return _decode_token(credentials.credentials)


def _decode_token(token: str) -> dict:
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience="authenticated",
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except PyJWKClientError as e:
        logger.error("JWT signing key lookup failed: %s", e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except jwt.InvalidTokenError as e:
        logger.error("JWT verification failed: %s", e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None
    is_admin: bool


def _csv_values(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def get_current_user(token: dict = Depends(verify_token)) -> CurrentUser:
    """Extract current user identity and lightweight admin flag from verified JWT."""
    return _current_user_from_payload(token)


def get_optional_current_user(token: dict | None = Depends(verify_optional_token)) -> CurrentUser | None:
    """Extract current user if a bearer token was provided."""
    if token is None:
        return None
    return _current_user_from_payload(token)


def _current_user_from_payload(token: dict) -> CurrentUser:
    user_id = token.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: no sub")

    email_value = token.get("email")
    email = email_value if isinstance(email_value, str) else None
    admin_user_ids = _csv_values(settings.admin_user_ids)
    admin_emails = {item.lower() for item in _csv_values(settings.admin_emails)}
    is_admin = user_id in admin_user_ids or bool(email and email.lower() in admin_emails)
    return CurrentUser(id=user_id, email=email, is_admin=is_admin)


def get_user_id(current_user: CurrentUser = Depends(get_current_user)) -> str:
    """Extract user_id from verified JWT."""
    return current_user.id
