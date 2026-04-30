from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import verify_user_token


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    return verify_user_token(authorization.removeprefix("Bearer ").strip(), db)


def whitelisted_user(user: User = Depends(current_user)) -> User:
    if not user.is_whitelisted:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Beta whitelist required")
    if user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User disabled")
    return user

