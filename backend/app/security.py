from datetime import datetime, timezone

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="darensub-auth")


def create_user_token(user: User) -> str:
    return _serializer().dumps({"uid": user.id, "openid": user.openid})


def verify_user_token(token: str, db: Session) -> User:
    try:
        data = _serializer().loads(token, max_age=60 * 60 * 24 * 30)
    except BadSignature as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
    user = db.get(User, int(data["uid"]))
    if not user or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User unavailable")
    return user


def create_admin_token() -> str:
    return _serializer().dumps({"admin": settings.admin_username})


def verify_admin_request(request: Request) -> None:
    token = request.cookies.get("admin_session")
    if not token:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    try:
        data = _serializer().loads(token, max_age=60 * 60 * 12)
    except BadSignature as exc:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"}) from exc
    if data.get("admin") != settings.admin_username:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})

