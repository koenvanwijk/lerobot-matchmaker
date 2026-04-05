"""
server.py — aiohttp signaling server.

Routes:
  POST /signal/{room}/{role}/send   — store a message sent by {role}
  GET  /signal/{room}/{role}/recv   — long-poll for messages sent by {role}
                                      (receiver polls the sender's queue)
  GET  /rooms                       — list active rooms (debug)
  GET  /health                      — health check

Each receiver gets a unique subscriber_id (via X-Subscriber-Id header or
auto-assigned on first poll) so multiple clients can receive independently.

Background cleanup removes rooms idle for >ROOM_TTL_SECONDS (default 5 min).
"""

from __future__ import annotations

import json
import logging
import uuid

from aiohttp import web

from .room import RoomRegistry, VALID_ROLES

logger = logging.getLogger(__name__)


def create_app() -> web.Application:
    registry = RoomRegistry()
    app = web.Application()
    app["registry"] = registry

    app.router.add_post("/signal/{room}/{role}/send", handle_send)
    app.router.add_get("/signal/{room}/{role}/recv", handle_recv)
    app.router.add_get("/rooms", handle_list_rooms)
    app.router.add_get("/health", handle_health)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    return app


async def on_startup(app: web.Application) -> None:
    app["registry"].start()


async def on_shutdown(app: web.Application) -> None:
    app["registry"].stop()


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

    The caller must include X-Subscriber-Id header with a stable UUID.
    If omitted, a new UUID is assigned and returned in the response header
    so the client can reuse it on subsequent polls.

    Returns 204 if no message arrives within the timeout (client retries).
    """
    room_name = request.match_info["room"]
    sender_role = request.match_info["role"]

    if sender_role not in VALID_ROLES:
        return web.Response(status=400, text=f"Invalid role: {sender_role!r}. Must be one of {VALID_ROLES}")

    subscriber_id = request.headers.get("X-Subscriber-Id") or str(uuid.uuid4())

    registry: RoomRegistry = request.app["registry"]
    room = registry.get_or_create(room_name)

    # Register subscriber if not yet known
    if subscriber_id not in room.queues[sender_role]:
        room.subscribe(sender_role, subscriber_id)
        logger.debug("Room %s: new subscriber %s for %s", room_name, subscriber_id[:8], sender_role)

    message = await room.get(sender_role=sender_role, subscriber_id=subscriber_id, timeout=25.0)

    headers = {"X-Subscriber-Id": subscriber_id}

    if message is None:
        return web.Response(status=204, headers=headers)

    return web.Response(
        status=200,
        content_type="application/json",
        text=json.dumps(message),
        headers=headers,
    )


async def handle_list_rooms(request: web.Request) -> web.Response:
    registry: RoomRegistry = request.app["registry"]
    return web.json_response({"rooms": registry.list_rooms()})


async def handle_health(request: web.Request) -> web.Response:
    registry: RoomRegistry = request.app["registry"]
    return web.json_response({"status": "ok", "rooms": len(registry._rooms)})
