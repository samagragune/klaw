"""Dashboard WebSocket.

Browsers cannot set headers on a WebSocket handshake, so the credential
arrives as a query parameter. It is validated exactly like a Bearer token and
the socket is bound to that user's channel - a client cannot subscribe to
another tenant's stream by asking for it.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.bus import get_bus, user_channel
from app.db import SessionLocal
from app.security import resolve_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])

HEARTBEAT_SECONDS = 25.0


@router.websocket("/v1/ws")
async def dashboard_stream(websocket: WebSocket, token: str = Query(...)) -> None:
    async with SessionLocal() as session:
        try:
            user = await resolve_user(token, session)
        except HTTPException:
            # 1008 = policy violation. Closing before accept would deny the
            # client any reason at all.
            await websocket.accept()
            await websocket.send_json({"type": "error", "message": "unauthorized"})
            await websocket.close(code=1008)
            return

    await websocket.accept()
    await websocket.send_json({"type": "connected", "user_id": user.id})

    bus = get_bus()
    async with bus.subscribe(user_channel(user.id)) as subscription:
        pump = asyncio.create_task(_pump(websocket, subscription))
        try:
            while True:
                # The client sends nothing meaningful; this read exists to
                # observe disconnects promptly.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:  # pragma: no cover - transport level
            logger.debug("dashboard socket closed", exc_info=True)
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump


async def _pump(websocket: WebSocket, subscription) -> None:
    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    subscription.get(), timeout=HEARTBEAT_SECONDS
                )
            except (asyncio.TimeoutError, TimeoutError):
                await websocket.send_json({"type": "heartbeat"})
                continue
            await websocket.send_json(message)
    except asyncio.CancelledError:
        raise
    except Exception:  # pragma: no cover - transport level
        logger.debug("stopped pumping to dashboard socket", exc_info=True)
