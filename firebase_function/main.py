"""
Firebase Cloud Functions matchmaker (firebase_functions SDK v0.5+).

Routes:
  POST /signal/{room}/{role}/send   — store message in Firestore
  GET  /signal/{room}/{role}/recv   — long-poll for messages
  GET  /rooms                       — list active rooms
  GET  /health                      — health check
"""

from __future__ import annotations

import json
import time
import uuid

from firebase_functions import https_fn
from firebase_admin import initialize_app, firestore as fs_admin
from google.cloud import firestore

initialize_app()

VALID_ROLES = {"operator", "robot"}
POLL_INTERVAL_S = 0.5
POLL_TIMEOUT_S = 25.0

_db: firestore.Client | None = None


def _get_db() -> firestore.Client:
    global _db
    if _db is None:
        _db = fs_admin.client()
    return _db


def _messages_ref(db: firestore.Client, room: str) -> firestore.CollectionReference:
    return db.collection("rooms").document(room).collection("messages")


@https_fn.on_request()
def matchmaker(req: https_fn.Request) -> https_fn.Response:
    path = req.path.rstrip("/")
    method = req.method

    if path == "/health" and method == "GET":
        return https_fn.Response(
            json.dumps({"status": "ok", "backend": "firestore"}),
            content_type="application/json",
        )

    if path == "/rooms" and method == "GET":
        db = _get_db()
        rooms = [{"name": doc.id} for doc in db.collection("rooms").stream()]
        return https_fn.Response(
            json.dumps({"rooms": rooms}),
            content_type="application/json",
        )

    parts = path.split("/")
    # ['', 'signal', room, role, 'send'|'recv']
    if len(parts) == 5 and parts[1] == "signal":
        room, role, action = parts[2], parts[3], parts[4]

        if role not in VALID_ROLES:
            return https_fn.Response(f"Invalid role: {role!r}", status=400)

        if action == "send" and method == "POST":
            try:
                payload = req.get_json(force=True)
                if payload is None:
                    raise ValueError
            except Exception:
                return https_fn.Response("Request body must be valid JSON", status=400)

            db = _get_db()
            _messages_ref(db, room).add({
                "sender_role": role,
                "payload": payload,
                "created_at": firestore.SERVER_TIMESTAMP,
                "consumed_by": [],
            })
            return https_fn.Response("ok")

        if action == "recv" and method == "GET":
            subscriber_id = req.headers.get("X-Subscriber-Id") or str(uuid.uuid4())
            db = _get_db()
            msgs = _messages_ref(db, room)
            deadline = time.monotonic() + POLL_TIMEOUT_S

            while time.monotonic() < deadline:
                docs = list(
                    msgs
                    .where(filter=firestore.FieldFilter("sender_role", "==", role))
                    .order_by("created_at")
                    .limit(20)
                    .stream()
                )
                for doc in docs:
                    data = doc.to_dict()
                    if subscriber_id not in (data.get("consumed_by") or []):
                        doc.reference.update({
                            "consumed_by": firestore.ArrayUnion([subscriber_id])
                        })
                        return https_fn.Response(
                            json.dumps(data["payload"]),
                            content_type="application/json",
                            headers={"X-Subscriber-Id": subscriber_id},
                        )
                time.sleep(POLL_INTERVAL_S)

            return https_fn.Response(
                "",
                status=204,
                headers={"X-Subscriber-Id": subscriber_id},
            )

    return https_fn.Response("Not found", status=404)
