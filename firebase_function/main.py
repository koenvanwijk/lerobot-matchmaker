"""
Firebase Cloud Functions matchmaker — same HTTP interface as the aiohttp server.

Routes (via single HTTPS function):
  POST /signal/{room}/{role}/send   — store message in Firestore
  GET  /signal/{room}/{role}/recv   — long-poll (short-poll loop) for messages
  GET  /rooms                       — list active rooms
  GET  /health                      — health check

Architecture:
  - Each message is a Firestore document under:
      rooms/{room}/messages/{auto_id}
      fields: sender_role, payload (dict), created_at (timestamp), consumed_by (list[str])
  - recv() polls Firestore every 0.5s for up to 25s, then returns 204.
  - Old messages cleaned up by a scheduled Cloud Function (cleanup_old_messages).

Deploy:
  cd firebase/
  pip install -r requirements.txt
  firebase deploy --only functions

Local dev:
  functions-framework --target=matchmaker --debug
  # or with the firebase emulator:
  firebase emulators:start --only functions,firestore
"""

from __future__ import annotations

import json
import logging
import time
import uuid

import functions_framework
from flask import Request, Response, jsonify
from google.cloud import firestore

logger = logging.getLogger(__name__)

VALID_ROLES = {"operator", "robot"}
POLL_INTERVAL_S = 0.5
POLL_TIMEOUT_S = 25.0
MSG_TTL_S = 300  # 5 minutes

_db: firestore.Client | None = None


def _get_db() -> firestore.Client:
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


def _messages_ref(db: firestore.Client, room: str) -> firestore.CollectionReference:
    return db.collection("rooms").document(room).collection("messages")


# ---------------------------------------------------------------------------
# Entrypoint

def _dispatch(request: Request) -> Response:
    """Router — separated from the decorator so tests can call it directly."""
    path = request.path.rstrip("/")
    method = request.method

    if path == "/health" and method == "GET":
        return _handle_health()

    if path == "/rooms" and method == "GET":
        return _handle_rooms()

    # /signal/{room}/{role}/send  or  /signal/{room}/{role}/recv
    parts = path.split("/")
    # parts: ['', 'signal', room, role, 'send'|'recv']
    if len(parts) == 5 and parts[1] == "signal":
        room, role, action = parts[2], parts[3], parts[4]
        if action == "send" and method == "POST":
            return _handle_send(room, role, request)
        if action == "recv" and method == "GET":
            return _handle_recv(room, role, request)

    return Response("Not found", status=404)


@functions_framework.http
def matchmaker(request: Request) -> Response:
    """Cloud Functions entrypoint — wraps _dispatch."""
    return _dispatch(request)


# ---------------------------------------------------------------------------
# Handlers

def _handle_health() -> Response:
    return jsonify({"status": "ok", "backend": "firestore"})


def _handle_rooms() -> Response:
    db = _get_db()
    rooms = [{"name": doc.id} for doc in db.collection("rooms").stream()]
    return jsonify({"rooms": rooms})


def _handle_send(room: str, role: str, request: Request) -> Response:
    if role not in VALID_ROLES:
        return Response(f"Invalid role: {role!r}", status=400)

    try:
        payload = request.get_json(force=True)
        if payload is None:
            raise ValueError("empty body")
    except Exception:
        return Response("Request body must be valid JSON", status=400)

    db = _get_db()
    _messages_ref(db, room).add({
        "sender_role": role,
        "payload": payload,
        "created_at": firestore.SERVER_TIMESTAMP,
        "consumed_by": [],
    })

    logger.info("Room %s: %s sent %s", room, role, payload.get("type", "?"))
    return Response("ok", status=200)


def _handle_recv(room: str, sender_role: str, request: Request) -> Response:
    """
    Long-poll: repeatedly query Firestore for unconsumed messages from sender_role.

    Firestore has no 'array_not_contains' filter, so we fetch recent messages
    and filter client-side. Marks each consumed doc with subscriber_id.

    Returns 200+JSON on first match, 204 after POLL_TIMEOUT_S (client retries).
    """
    if sender_role not in VALID_ROLES:
        return Response(f"Invalid role: {sender_role!r}", status=400)

    subscriber_id = request.headers.get("X-Subscriber-Id") or str(uuid.uuid4())
    db = _get_db()
    msgs = _messages_ref(db, room)
    deadline = time.monotonic() + POLL_TIMEOUT_S

    while time.monotonic() < deadline:
        docs = list(
            msgs
            .where(filter=firestore.FieldFilter("sender_role", "==", sender_role))
            .order_by("created_at")
            .limit(20)
            .stream()
        )

        for doc in docs:
            data = doc.to_dict()
            if subscriber_id not in (data.get("consumed_by") or []):
                # Atomically mark as consumed by this subscriber
                doc.reference.update({
                    "consumed_by": firestore.ArrayUnion([subscriber_id])
                })
                return Response(
                    json.dumps(data["payload"]),
                    status=200,
                    content_type="application/json",
                    headers={"X-Subscriber-Id": subscriber_id},
                )

        time.sleep(POLL_INTERVAL_S)

    return Response(
        "",
        status=204,
        headers={"X-Subscriber-Id": subscriber_id},
    )


# ---------------------------------------------------------------------------
# Cleanup scheduled function

@functions_framework.http
def cleanup_old_messages(request: Request) -> Response:
    """
    Delete Firestore message documents older than MSG_TTL_S.
    Invoke via Cloud Scheduler (e.g. every 5 minutes).
    """
    db = _get_db()
    deleted = 0

    for room_doc in db.collection("rooms").stream():
        msgs = room_doc.reference.collection("messages")
        for msg in msgs.stream():
            data = msg.to_dict()
            created = data.get("created_at")
            if created and hasattr(created, "timestamp") and time.time() - created.timestamp() > MSG_TTL_S:
                msg.reference.delete()
                deleted += 1

    logger.info("Cleanup: deleted %d old messages", deleted)
    return jsonify({"deleted": deleted})
