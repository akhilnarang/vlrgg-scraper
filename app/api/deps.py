from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

http_bearer = HTTPBearer()


def verify_token(
    token_data: HTTPAuthorizationCredentials = Depends(http_bearer),
) -> None:
    if token_data.credentials != settings.SECRET_KEY:
        raise HTTPException(detail="Invalid token", status_code=status.HTTP_401_UNAUTHORIZED)
