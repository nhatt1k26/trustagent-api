"""Common dependencies: JWT auth, get current user."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import AgentCredential, AgentDetail

security = HTTPBearer()
ALGORITHM = "HS256"


def get_current_username(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
        return username
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")


def get_current_agent(
    username: str = Depends(get_current_username),
    db: Session = Depends(get_db),
) -> AgentDetail:
    agent = db.query(AgentDetail).filter_by(username=username).first()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent profile not found")
    return agent


def require_privilege(code: str):
    """Factory tạo dependency chặn truy cập nếu agent chưa mở khoá đặc quyền `code`.

    Dùng cho các tính năng đặc quyền theo cấp (Zalo OA broadcast, chatbot cao cấp...):

        @router.post("/zalo/broadcast")
        def broadcast(agent = Depends(require_privilege("ZALO_OA_BROADCAST"))):
            ...
    """
    def _dep(agent: AgentDetail = Depends(get_current_agent)) -> AgentDetail:
        from app import achievements as ach
        from app import compensation as comp
        level = comp.rank_to_level(agent.rank)
        if not ach.has_privilege(level, code):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Tính năng này chỉ dành cho cấp bậc cao hơn. Hãy thăng cấp để mở khoá.",
            )
        return agent
    return _dep
