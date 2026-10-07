"""AI Chat endpoints — tích hợp Kiro CLI qua ACP.

Yêu cầu đăng nhập (JWT). Mỗi phiên chat tương ứng một tiến trình kiro-cli riêng,
được quản lý bởi KiroACPManager. FE gọi:

    POST   /api/v1/chat/sessions            -> tạo phiên mới
    POST   /api/v1/chat/sessions/{sid}/message         -> gửi prompt (trả 1 lần)
    POST   /api/v1/chat/sessions/{sid}/message/stream  -> gửi prompt (SSE stream)
    DELETE /api/v1/chat/sessions/{sid}       -> đóng phiên
    GET    /api/v1/chat/sessions             -> liệt kê phiên đang mở
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.deps import get_current_username
from app.kiro_acp import KiroACPManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


class PromptRequest(BaseModel):
    message: str


class SessionResponse(BaseModel):
    session_id: str
    pid: int
    created_at: float


class PromptResponse(BaseModel):
    response: str
    stop_reason: str | None = None


@router.post("/sessions", response_model=SessionResponse)
async def create_session(_: str = Depends(get_current_username)):
    """Tạo một phiên chat Kiro mới (spawn kiro-cli acp)."""
    manager = KiroACPManager.get_instance()
    try:
        info = await manager.create_session()
        return SessionResponse(**info)
    except Exception as e:
        logger.error(f"[chat] create_session failed: {e!r}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Không tạo được phiên Kiro: {e}")


@router.post("/sessions/{session_id}/message", response_model=PromptResponse)
async def send_message(
    session_id: str,
    body: PromptRequest,
    _: str = Depends(get_current_username),
):
    """Gửi một prompt và nhận toàn bộ phản hồi một lần."""
    if not body.message.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nội dung tin nhắn trống")
    manager = KiroACPManager.get_instance()
    try:
        text, stop_reason = await manager.send_prompt(session_id, body.message)
        return PromptResponse(response=text, stop_reason=stop_reason)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    except Exception as e:
        logger.error(f"[chat] send_message failed: {e!r}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Lỗi khi gọi Kiro: {e}")


@router.post("/sessions/{session_id}/message/stream")
async def send_message_stream(
    session_id: str,
    body: PromptRequest,
    _: str = Depends(get_current_username),
):
    """Gửi prompt và stream phản hồi theo dạng Server-Sent Events (SSE)."""
    if not body.message.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nội dung tin nhắn trống")

    manager = KiroACPManager.get_instance()

    async def event_generator():
        try:
            async for chunk in manager.send_prompt_streaming(session_id, body.message):
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        except ValueError as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)}, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.error(f"[chat] stream failed: {e!r}")
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/sessions/{session_id}")
async def close_session(session_id: str, _: str = Depends(get_current_username)):
    """Đóng và giải phóng một phiên chat."""
    manager = KiroACPManager.get_instance()
    await manager.kill_session(session_id)
    return {"status": "closed", "session_id": session_id}


@router.get("/sessions")
async def list_sessions(_: str = Depends(get_current_username)):
    """Liệt kê các phiên chat đang mở."""
    manager = KiroACPManager.get_instance()
    return {"sessions": manager.list_sessions()}
