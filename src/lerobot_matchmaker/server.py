"""
server.py — aiohttp signaling server.

Routes:
  POST /signal/{room}/{role}/send   — store a message sent by {role}
  GET  /signal/{room}/{role}/recv   — long-poll for messages sent by {role}
                                      (receiver polls the sender's queue)
  GET  /rooms                       — list active rooms (debug)
  GET  /health                      — health check

The SignalingClient in lerobot-remote-transport uses these endpoints.
"""

from __future__ import annotations

import json
import logging

from aiohttp import web

from .room import RoomRegistry, VALID_ROLES

logger = logging.getLogger(__name__)


def create_app() -> web.Application:
    registry = RoomRegistry()
    app = web.Application()

    # Attach registry so handlers can access it
    app["registry"] = registry

    app.router.add_post("/signal/{room}/{role}/send", handle_send)
    app.router.add_get("/signal/{room}/{role}/recv", handle_recv)
    app.router.add_get("/rooms", handle_list_rooms)
    app.router.add_get("/health", handle_health)

    return app


async def handle_send(request: web.Request) -> web.Response:
    room_name = request.match_info["room"]
    role = request.match_info["role"]

    if role not in VALID_ROLES:
        return web.Response(status=400, text=f"Invalid role: {role!r}. Must be one of {VALID_ROLES}")

    try:
        message = await request.json()
    except Exception:
        return web.Response(status=400, text="Request body must be valid JSON")

    registry: RoomRegistry = request.app["registry"]
    room = registry.get_or_create(room_name)
    room.connected.add(role)
    await room.put(sender_role=role, message=message)

    logger.info("Room %s: %s sent %s", room_name, role, message.get("type", "?"))
    return web.Response(status=200, text="ok")


async def handle_recv(request: web.Request) -> web.Response:
    """
    Long-poll: wait for a message sent by {role} and return it.
    Returns 204 if no message arrives within the timeout (client should retry).
    """
    room_name = request.match_info["room"]
    sender_role = request.match_info["role"]

    if sender_role not in VALID_ROLES:
        return web.Response(status=400, text=f"Invalid role: {sender_role!r}. Must be one of {VALID_ROLES}")

    registry: RoomRegistry = request.app["registry"]
    room = registry.get_or_create(room_name)

    message = await room.get(sender_role=sender_role, timeout=25.0)

    if message is None:
        return web.Response(status=204)  # timeout — client loops back

    return web.Response(
        status=200,
        content_type="application/json",
        text=json.dumps(message),
    )


async def handle_list_rooms(request: web.Request) -> web.Response:
    registry: RoomRegistry = request.app["registry"]
    return web.json_response({"rooms": registry.list_rooms()})


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})
