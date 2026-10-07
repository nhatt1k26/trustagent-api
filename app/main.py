"""TrustAgent API - FastAPI backend service for Trust-Agent-AIO frontend."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.database import Base, engine
from app.kiro_acp import KiroACPManager
from app.routers import (
    agent, appointment, chat, consultation, contract, lead, notification, register, tree,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Auto-create tables if they don't exist
Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Khởi động/tắt manager Kiro ACP cùng vòng đời ứng dụng."""
    manager = KiroACPManager.get_instance()
    await manager.start()
    try:
        yield
    finally:
        await manager.stop()


app = FastAPI(title="TrustAgent API", version="1.0.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log chi tiết lỗi validation 422 ra console."""
    logger.error(f"Validation error on {request.method} {request.url}")
    logger.error(f"Body: {await request.body()}")
    logger.error(f"Errors: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )

# CORS - allow FE dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(register.router)
app.include_router(agent.router)
app.include_router(lead.router)
app.include_router(appointment.router)
app.include_router(consultation.router)
app.include_router(contract.router)
app.include_router(notification.router)
app.include_router(tree.router)
app.include_router(chat.router)


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8002, reload=True)
