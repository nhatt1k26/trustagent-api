"""Tích hợp Kiro CLI qua ACP (Agent Client Protocol) — hỗ trợ đa phiên.

Mỗi phiên (session) là một tiến trình con `kiro-cli acp` độc lập, giữ ngữ cảnh
hội thoại riêng và có thể chạy song song. Phiên được định danh bằng ``session_id``.
Các phiên nhàn rỗi quá ``KIRO_TIMEOUT`` giây sẽ tự bị thu hồi.

Kiến trúc:
- Một thread nền chạy event loop asyncio riêng (ProactorEventLoop trên Windows để
  hỗ trợ subprocess), tách khỏi event loop của FastAPI.
- Manager dạng singleton quản lý map ``session_id -> ACPSession``.

API:
    manager = KiroACPManager.get_instance()
    info = await manager.create_session()
    text, stop = await manager.send_prompt(info["session_id"], "xin chào")
    async for chunk in manager.send_prompt_streaming(sid, "..."): ...
    await manager.kill_session(info["session_id"])
"""

from __future__ import annotations

import asyncio
import asyncio.subprocess as aio_subprocess
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator

from acp import PROTOCOL_VERSION, Client, RequestError, connect_to_agent, text_block
from acp.core import ClientSideConnection
from acp.schema import (
    AgentMessageChunk,
    ClientCapabilities,
    Implementation,
    PermissionOption,
    RequestPermissionResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UsageUpdate,
)

from app.config import settings

logger = logging.getLogger(__name__)


# ─── ACP Client (xử lý callback từ kiro-cli) ─────────────────────────────────
class KiroACPClient(Client):
    """ACP Client: tự động phê duyệt tool call và bắt (capture) text stream."""

    def __init__(self, session_label: str = ""):
        self._label = session_label
        self.captured_text = ""
        self._capturing = False
        self._stream_queue: asyncio.Queue | None = None
        self.last_usage_update: dict | None = None

    def start_capture(self, stream_queue: asyncio.Queue | None = None):
        self.captured_text = ""
        self._capturing = True
        self._stream_queue = stream_queue
        self.last_usage_update = None

    def stop_capture(self) -> str:
        self._capturing = False
        self._stream_queue = None
        return self.captured_text

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        tool_name = getattr(tool_call, "name", "unknown")
        logger.debug(f"[KiroACP:{self._label}] Auto-approve tool: {tool_name}")
        return RequestPermissionResponse(outcome={"outcome": "approve"})

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        if isinstance(update, AgentMessageChunk):
            content = update.content
            if isinstance(content, TextContentBlock):
                text = content.text
                if self._capturing:
                    self.captured_text += text
                    if self._stream_queue:
                        await self._stream_queue.put({"type": "output", "text": text})
        elif isinstance(update, (ToolCallStart, ToolCallProgress)):
            name = getattr(update, "name", None) or getattr(
                getattr(update, "tool_call", None), "name", "?"
            )
            status = getattr(update, "status", "")
            logger.info(f"[KiroACP:{self._label}] Tool: {name} | {status}")
            if self._stream_queue:
                await self._stream_queue.put({"type": "tool", "text": f"[{name}] {status}"})
        elif isinstance(update, UsageUpdate):
            usage_data: dict = {
                "tokens_used": getattr(update, "used", None),
                "context_size": getattr(update, "size", None),
            }
            cost = getattr(update, "cost", None)
            if cost:
                usage_data["cost_amount"] = cost.amount
                usage_data["cost_currency"] = cost.currency
            self.last_usage_update = usage_data
            logger.info(f"[KiroACP:{self._label}] Usage: {usage_data}")
        else:
            logger.debug(f"[KiroACP:{self._label}] Other: {type(update).__name__}")

    # Các callback filesystem/terminal: từ chối (không cần thiết cho chat) ──────
    async def write_text_file(self, *a, **k):
        raise RequestError.method_not_found("fs/write_text_file")

    async def read_text_file(self, *a, **k):
        raise RequestError.method_not_found("fs/read_text_file")

    async def create_terminal(self, *a, **k):
        raise RequestError.method_not_found("terminal/create")

    async def terminal_output(self, *a, **k):
        raise RequestError.method_not_found("terminal/output")

    async def release_terminal(self, *a, **k):
        raise RequestError.method_not_found("terminal/release")

    async def wait_for_terminal_exit(self, *a, **k):
        raise RequestError.method_not_found("terminal/wait_for_exit")

    async def kill_terminal(self, *a, **k):
        raise RequestError.method_not_found("terminal/kill")

    async def create_elicitation(self, *a, **k):
        from acp.schema import DeclineElicitationResponse

        return DeclineElicitationResponse(action="decline")

    async def complete_elicitation(self, *a, **k):
        pass

    async def ext_method(self, method: str, params: dict) -> dict:
        return {}

    async def ext_notification(self, method: str, params: dict) -> None:
        pass


# ─── Thread chứa event loop nền ──────────────────────────────────────────────
class _ACPEventLoopThread:
    """Event loop asyncio chạy trong thread nền (hỗ trợ subprocess trên Windows)."""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="kiro-acp-loop")
        self._thread.start()
        while self._loop is None:
            time.sleep(0.01)
        logger.info(f"[KiroACP] Background loop started ({type(self._loop).__name__})")

    def _run(self):
        if sys.platform == "win32":
            self._loop = asyncio.ProactorEventLoop()
        else:
            self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def stop(self):
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None

    def run_coroutine(self, coro):
        if self._loop is None:
            raise RuntimeError("ACP event loop not started")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("ACP event loop not started")
        return self._loop


_acp_loop = _ACPEventLoopThread()


# ─── Dữ liệu phiên ───────────────────────────────────────────────────────────
@dataclass
class ACPSession:
    session_id: str
    process: asyncio.subprocess.Process
    connection: ClientSideConnection
    client: KiroACPClient
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)


# ─── Manager đa phiên ────────────────────────────────────────────────────────
class KiroACPManager:
    """Quản lý nhiều phiên kiro-cli ACP; mỗi phiên là một subprocess độc lập."""

    _instance: "KiroACPManager | None" = None

    def __init__(self):
        self._sessions: dict[str, ACPSession] = {}
        self._sessions_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "KiroACPManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def start(self):
        """Khởi động loop nền + reaper (gọi trong app lifespan)."""
        _acp_loop.start()
        _acp_loop.run_coroutine(self._reaper_loop())
        logger.info("[KiroACP] Manager started (multi-session)")

    async def stop(self):
        """Giết toàn bộ phiên và dừng loop nền."""
        for sid in list(self._sessions.keys()):
            try:
                await self.kill_session(sid)
            except Exception:
                pass
        _acp_loop.stop()
        logger.info("[KiroACP] Manager stopped")

    # ─── Public API ──────────────────────────────────────────────────────
    async def create_session(self) -> dict:
        future = _acp_loop.run_coroutine(self._create_session_impl())
        return await asyncio.wrap_future(future)

    async def send_prompt(self, session_id: str, message: str) -> tuple[str, str | None]:
        self._validate_session(session_id)
        future = _acp_loop.run_coroutine(self._send_prompt_impl(session_id, message))
        return await asyncio.wrap_future(future)

    async def send_prompt_streaming(
        self, session_id: str, message: str
    ) -> AsyncGenerator[dict, None]:
        self._validate_session(session_id)
        bridge_queue: asyncio.Queue = asyncio.Queue()
        main_loop = asyncio.get_event_loop()
        future = _acp_loop.run_coroutine(
            self._send_prompt_streaming_impl(session_id, message, bridge_queue, main_loop)
        )
        while True:
            try:
                chunk = await asyncio.wait_for(bridge_queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                if future.done():
                    exc = future.exception()
                    if exc:
                        yield {"type": "error", "text": f"{type(exc).__name__}: {exc!r}"}
                        yield {"type": "status", "text": "failed"}
                    return
                continue
            if chunk is None:
                return
            yield chunk

    async def kill_session(self, session_id: str):
        future = _acp_loop.run_coroutine(self._kill_session_impl(session_id))
        await asyncio.wrap_future(future)

    def list_sessions(self) -> list[dict]:
        with self._sessions_lock:
            return [
                {
                    "session_id": s.session_id,
                    "pid": s.process.pid,
                    "created_at": s.created_at,
                    "last_active": s.last_active,
                    "active": s.process.returncode is None,
                }
                for s in self._sessions.values()
            ]

    # ─── Kiểm tra hợp lệ ─────────────────────────────────────────────────
    def _validate_session(self, session_id: str):
        with self._sessions_lock:
            if session_id not in self._sessions:
                raise ValueError(f"Session not found: {session_id}")
            if self._sessions[session_id].process.returncode is not None:
                raise ValueError(f"Session process is dead: {session_id}")

    # ─── Triển khai (chạy trong loop nền) ────────────────────────────────
    def _build_env(self) -> dict:
        env = os.environ.copy()
        work_dir = Path(settings.KIRO_WORK_DIR).expanduser().resolve()
        env["KIRO_HOME"] = str(work_dir)
        # API key do người dùng tự set trong .env — truyền cho kiro-cli.
        if settings.KIRO_API_KEY:
            env["KIRO_API_KEY"] = settings.KIRO_API_KEY
        return env

    async def _create_session_impl(self) -> dict:
        work_dir = Path(settings.KIRO_WORK_DIR).expanduser().resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        env = self._build_env()

        args = [settings.KIRO_CLI_PATH, "acp", "--trust-all-tools"]
        if settings.KIRO_AGENT:
            args += ["--agent", settings.KIRO_AGENT]
        if settings.KIRO_MODEL:
            args += ["--model", settings.KIRO_MODEL]

        logger.info(f"[KiroACP] Spawning: {' '.join(args)} | cwd={work_dir}")
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=aio_subprocess.PIPE,
            stdout=aio_subprocess.PIPE,
            stderr=aio_subprocess.PIPE,
            cwd=str(work_dir),
            env=env,
        )
        if proc.stdin is None or proc.stdout is None:
            raise RuntimeError("Failed to open stdio pipes to kiro-cli")
        logger.info(f"[KiroACP] Process spawned (pid={proc.pid})")

        client = KiroACPClient(session_label=f"pid={proc.pid}")
        conn = connect_to_agent(client, proc.stdin, proc.stdout)

        # Bắt tay ACP
        try:
            await conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(
                    fs={"readTextFile": False, "writeTextFile": False},
                    terminal=False,
                ),
                client_info=Implementation(
                    name="trustagent-api",
                    title="TrustAgent Kiro Integration",
                    version="1.0.0",
                ),
            )
        except Exception as e:
            stderr = await self._read_stderr(proc)
            logger.error(f"[KiroACP] Init FAILED: {type(e).__name__}: {e} | stderr: {stderr}")
            proc.terminate()
            raise RuntimeError(f"ACP init failed: {e} | stderr: {stderr}") from e

        try:
            session_result = await conn.new_session(cwd=str(work_dir), mcp_servers=[])
        except Exception as e:
            stderr = await self._read_stderr(proc)
            logger.error(f"[KiroACP] new_session FAILED: {type(e).__name__}: {e} | stderr: {stderr}")
            proc.terminate()
            raise RuntimeError(f"new_session failed: {e} | stderr: {stderr}") from e

        session_id = session_result.session_id
        logger.info(f"[KiroACP] Session created: {session_id} (pid={proc.pid})")

        acp_session = ACPSession(
            session_id=session_id, process=proc, connection=conn, client=client
        )
        with self._sessions_lock:
            self._sessions[session_id] = acp_session

        return {
            "session_id": session_id,
            "pid": proc.pid,
            "created_at": acp_session.created_at,
        }

    async def _send_prompt_impl(self, session_id: str, message: str) -> tuple[str, str | None]:
        with self._sessions_lock:
            session = self._sessions[session_id]
        session.last_active = time.time()
        session.client.start_capture()
        logger.info(f"[KiroACP:{session_id[:12]}] >>> {message[:200]}")
        try:
            result = await session.connection.prompt(
                session_id=session.session_id,
                prompt=[text_block(message)],
            )
            response = session.client.stop_capture()
            stop_reason = getattr(result, "stop_reason", None)
            logger.info(
                f"[KiroACP:{session_id[:12]}] <<< (stop={stop_reason}, len={len(response)})"
            )
            return response, stop_reason
        except Exception as e:
            session.client.stop_capture()
            logger.error(f"[KiroACP:{session_id[:12]}] Prompt error: {type(e).__name__}: {e!r}")
            raise

    async def _send_prompt_streaming_impl(
        self,
        session_id: str,
        message: str,
        bridge_queue: asyncio.Queue,
        main_loop: asyncio.AbstractEventLoop,
    ):
        with self._sessions_lock:
            session = self._sessions.get(session_id)
        if not session:
            main_loop.call_soon_threadsafe(
                bridge_queue.put_nowait, {"type": "error", "text": f"Session not found: {session_id}"}
            )
            main_loop.call_soon_threadsafe(bridge_queue.put_nowait, None)
            return

        session.last_active = time.time()
        internal_queue: asyncio.Queue = asyncio.Queue()
        session.client.start_capture(stream_queue=internal_queue)
        logger.info(f"[KiroACP:{session_id[:12]}] >>> (streaming) {message[:200]}")
        start_time = time.time()

        prompt_task = asyncio.create_task(
            session.connection.prompt(
                session_id=session.session_id,
                prompt=[text_block(message)],
            )
        )

        full_response = ""
        try:
            while not prompt_task.done():
                try:
                    chunk = await asyncio.wait_for(internal_queue.get(), timeout=1.0)
                    if chunk.get("type") == "output":
                        full_response += chunk.get("text", "")
                    main_loop.call_soon_threadsafe(bridge_queue.put_nowait, chunk)
                except asyncio.TimeoutError:
                    continue

            while not internal_queue.empty():
                chunk = internal_queue.get_nowait()
                if chunk.get("type") == "output":
                    full_response += chunk.get("text", "")
                main_loop.call_soon_threadsafe(bridge_queue.put_nowait, chunk)

            result = await prompt_task
            stop_reason = getattr(result, "stop_reason", None)
            elapsed = time.time() - start_time
            logger.info(
                f"[KiroACP:{session_id[:12]}] <<< (streaming, stop={stop_reason}, "
                f"len={len(full_response)}, elapsed={elapsed:.1f}s)"
            )

            status_chunk = {
                "type": "status",
                "text": "done",
                "stop_reason": stop_reason,
                "elapsed_seconds": round(elapsed, 1),
            }
            if session.client.last_usage_update:
                status_chunk["usage"] = session.client.last_usage_update
            main_loop.call_soon_threadsafe(bridge_queue.put_nowait, status_chunk)
        except Exception as e:
            import traceback

            err_detail = f"{type(e).__name__}: {e!r}"
            logger.error(
                f"[KiroACP:{session_id[:12]}] Streaming error: {err_detail}\n{traceback.format_exc()}"
            )
            main_loop.call_soon_threadsafe(
                bridge_queue.put_nowait, {"type": "error", "text": err_detail}
            )
            main_loop.call_soon_threadsafe(
                bridge_queue.put_nowait, {"type": "status", "text": "failed"}
            )
        finally:
            session.client.stop_capture()
            main_loop.call_soon_threadsafe(bridge_queue.put_nowait, None)

    async def _kill_session_impl(self, session_id: str):
        with self._sessions_lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return
        proc = session.process
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
            logger.info(f"[KiroACP] Killed session {session_id[:12]} (pid={proc.pid})")

    async def _reaper_loop(self):
        idle_timeout = settings.KIRO_TIMEOUT
        while True:
            await asyncio.sleep(60)
            now = time.time()
            with self._sessions_lock:
                idle_ids = [
                    sid
                    for sid, s in self._sessions.items()
                    if now - s.last_active > idle_timeout
                ]
            for sid in idle_ids:
                logger.info(f"[KiroACP] Reaping idle session: {sid[:12]}")
                await self._kill_session_impl(sid)

    async def _read_stderr(self, proc: asyncio.subprocess.Process) -> str:
        try:
            if proc.stderr is None:
                return ""
            data = await asyncio.wait_for(proc.stderr.read(4096), timeout=1.0)
            return data.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""
