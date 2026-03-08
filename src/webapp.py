"""Web application module for the DisplayBoard project.

This module hosts the web UI and WebSocket API.

- HTTP: serves the Jinja2-rendered index page and static assets
- WS: pushes full state snapshots to all connected clients
- WS: accepts commands (e.g. set_mode) from clients
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from local_enumerations import Command

if TYPE_CHECKING:
    from threading import Event

    from sc_utility import SCConfigManager, SCLogger

    from controller import AppController


def _get_repo_root() -> Path:
    # src/webapp.py -> repo_root
    return Path(__file__).resolve().parent.parent


def _validate_access_key(config: SCConfigManager, logger: SCLogger, key_from_request: str | None) -> bool:
    expected_key = os.environ.get("WEBAPP_ACCESS_KEY")
    if not expected_key:
        expected_key = config.get("Website", "AccessKey")
    if expected_key is None:
        return True
    if isinstance(expected_key, str) and not expected_key.strip():
        # Current behavior: empty AccessKey means open access.
        return True

    if key_from_request is None:
        logger.log_message("Missing access key.", "warning")
        return False
    key = key_from_request.strip()
    if not key:
        logger.log_message("Blank access key used.", "warning")
        return False
    if key != expected_key:
        logger.log_message("Invalid access key used.", "warning")
        return False
    return True


@dataclass
class WebAppNotifier:
    """Thread-safe notifier used by AppController to trigger WS broadcasts."""

    loop: asyncio.AbstractEventLoop | None = None
    queue: asyncio.Queue[None] | None = None

    def bind(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[None]) -> None:
        self.loop = loop
        self.queue = queue

    def notify(self) -> None:
        loop = self.loop
        queue = self.queue
        if loop is None or queue is None:
            return

        def _enqueue() -> None:
            # If we're already backed up, a later snapshot will catch up.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)

        loop.call_soon_threadsafe(_enqueue)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)

    async def broadcast_json(self, message: dict[str, Any]) -> None:
        text = json.dumps(message)
        async with self._lock:
            targets = list(self._connections)

        for ws in targets:
            try:
                await ws.send_text(text)
            except (RuntimeError, WebSocketDisconnect):
                await self.disconnect(ws)


def _configure_app_state(
    app: FastAPI,
    controller: AppController,
    config: SCConfigManager,
    logger: SCLogger,
    templates: Jinja2Templates,
    notifier: WebAppNotifier,
    manager: ConnectionManager,
) -> None:
    app.state.notifier = notifier
    app.state.manager = manager
    app.state.controller = controller
    app.state.config = config
    app.state.logger = logger
    app.state.templates = templates


def _register_routes(app: FastAPI, controller: AppController, config: SCConfigManager, logger: SCLogger, templates: Jinja2Templates, manager: ConnectionManager, notifier: WebAppNotifier) -> None:
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Any:
        key = request.query_params.get("key")
        if not _validate_access_key(config, logger, key):
            return HTMLResponse("Access forbidden.", status_code=403)

        snapshot = await asyncio.to_thread(controller.get_webapp_data)
        if not snapshot:
            logger.log_message("No web output data available yet", "warning")
            return HTMLResponse("no output data available yet", status_code=503)

        # Determine which board to serve.
        # ?board=<index> selects by zero-based position in DisplayBoards.Boards list.
        boards: list = config.get("DisplayBoards", "Boards", default=[]) or []
        try:
            board_index = int(request.query_params.get("board", 0))
        except (ValueError, TypeError):
            board_index = 0
        board_index = max(0, min(board_index, len(boards) - 1)) if boards else 0

        board_cfg = boards[board_index] if boards else {}
        template_file = board_cfg.get("Template", "board1.html")
        board_name = board_cfg.get("Name", f"Board {board_index + 1}")

        return templates.TemplateResponse(
            template_file,
            {
                "request": request,
                "global_data": snapshot.get("global", {}),
                "board_name": board_name,
                "board_index": board_index,
                "board_count": len(boards),
            },
        )

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        key = ws.query_params.get("key")
        if not _validate_access_key(config, logger, key):
            await ws.close(code=1008)
            return

        await manager.connect(ws)
        try:
            # Send initial snapshot
            snapshot = await asyncio.to_thread(controller.get_webapp_data)
            await ws.send_text(json.dumps({"type": "state_update", "state": snapshot}))

            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if msg.get("type") != "command":
                    continue

                # Note: Just and example command handler; you can define your own command structure and actions.
                action = msg.get("action")
                if action == "do_a_thing":
                    controller.post_command(Command("do_a_thing", {"arg1": 1}))
                    notifier.notify()
        except WebSocketDisconnect:
            await manager.disconnect(ws)
        except RuntimeError:
            await manager.disconnect(ws)


def create_asgi_app(controller: AppController, config: SCConfigManager, logger: SCLogger) -> tuple[FastAPI, WebAppNotifier]:
    repo_root = _get_repo_root()
    templates = Jinja2Templates(directory=str(repo_root / "templates"))
    notifier = WebAppNotifier()
    manager = ConnectionManager()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        loop = asyncio.get_running_loop()
        update_queue: asyncio.Queue[None] = asyncio.Queue(maxsize=100)
        notifier.bind(loop, update_queue)

        async def _broadcast_worker() -> None:
            try:
                while True:
                    await update_queue.get()
                    # Coalesce bursts into a single snapshot
                    while True:
                        try:
                            update_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    snapshot = await asyncio.to_thread(controller.get_webapp_data)
                    await manager.broadcast_json({"type": "state_update", "state": snapshot})
            except asyncio.CancelledError:
                return

        broadcast_task = loop.create_task(_broadcast_worker())
        try:
            yield
        finally:
            broadcast_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await broadcast_task

    app = FastAPI(lifespan=lifespan)

    # Serve static assets at /static
    app.mount("/static", StaticFiles(directory=str(repo_root / "static")), name="static")

    _configure_app_state(app, controller, config, logger, templates, notifier, manager)
    _register_routes(app, controller, config, logger, templates, manager, notifier)

    return app, notifier


def serve_asgi_blocking(app: FastAPI, config: SCConfigManager, logger: SCLogger, stop_event: Event):
    """Run an ASGI server in the current thread with cooperative shutdown using stop_event."""
    host_raw = config.get("Website", "HostingIP", default="127.0.0.1")
    host = host_raw if isinstance(host_raw, str) and host_raw else "127.0.0.1"
    port = int(config.get("Website", "Port", default=8080) or 8080)  # pyright: ignore[reportArgumentType]

    # Uvicorn log config can be noisy; keep our SCLogger as the source of truth.
    uv_config = uvicorn.Config(app, host=host, port=port, log_level="warning", reload=False)
    server = uvicorn.Server(uv_config)
    # Running under ThreadManager in a non-main thread: avoid installing signal handlers.
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]

    async def _run() -> None:
        async def _stop_watcher() -> None:
            # Block in a worker thread until the threading.Event is set.
            await asyncio.to_thread(stop_event.wait)
            server.should_exit = True

        watcher = asyncio.create_task(_stop_watcher())
        try:
            logger.log_message(f"Web server listening on http://{host}:{port}", "summary")
            await server.serve()
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
            logger.log_message("Web server shutdown complete.", "detailed")

    try:
        asyncio.run(_run())
    except asyncio.CancelledError:
        # Can occur if background tasks are cancelled during interpreter shutdown.
        logger.log_message("Web server cancelled during shutdown.", "debug")
