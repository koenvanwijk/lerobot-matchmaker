"""
tests/test_firebase.py — unit tests for the Firebase Cloud Function matchmaker.

Tests run entirely offline using a mock Firestore client (no real GCP needed).
The mock mirrors the Firestore data model: rooms/{room}/messages/{id}.
"""

from __future__ import annotations

import json
import time
import uuid
from unittest.mock import patch, MagicMock

import pytest
from flask import Flask


# ---------------------------------------------------------------------------
# Minimal in-memory Firestore mock

class MockFieldFilter:
    def __init__(self, field_path, op_string, value):
        self.field_path = field_path
        self.op_string = op_string
        self.value = value


class MockArrayUnion:
    def __init__(self, values):
        self._value = values


class MockServerTimestamp:
    pass


class MockDocument:
    def __init__(self, doc_id: str, data: dict, ref: "MockDocRef"):
        self.id = doc_id
        self._data = data
        self.reference = ref

    def to_dict(self) -> dict:
        return dict(self._data)


class MockDocRef:
    def __init__(self, store: "MockStore", path: tuple):
        self._store = store
        self._path = path

    def collection(self, name: str) -> "MockCollRef":
        return MockCollRef(self._store, self._path + (name,))

    def update(self, updates: dict) -> None:
        doc = self._store.get(self._path)
        if doc is None:
            return
        for key, value in updates.items():
            if isinstance(value, MockArrayUnion):
                existing = doc.get(key, [])
                doc[key] = existing + [v for v in value._value if v not in existing]
            else:
                doc[key] = value

    def delete(self) -> None:
        self._store.delete(self._path)

    def stream(self):
        return iter([])


_msg_counter = 0

class MockCollRef:
    def __init__(self, store: "MockStore", path: tuple):
        self._store = store
        self._path = path
        self._filters: list = []
        self._order: str | None = None
        self._lim: int | None = None

    def document(self, name: str) -> MockDocRef:
        return MockDocRef(self._store, self._path + (name,))

    def add(self, data: dict) -> tuple:
        doc_id = str(uuid.uuid4())
        full_path = self._path + (doc_id,)
        global _msg_counter
        _msg_counter += 1
        stored = {}
        for k, v in data.items():
            if hasattr(v, '__class__') and v.__class__.__name__ in ('MockServerTimestamp', 'MagicMock', 'NonCallableMagicMock'):
                stored[k] = float(_msg_counter)
            else:
                stored[k] = v
        self._store.set(full_path, stored)
        return (None, MockDocRef(self._store, full_path))

    def where(self, filter=None, **kwargs) -> "MockCollRef":
        copy = MockCollRef(self._store, self._path)
        copy._filters = list(self._filters) + ([filter] if filter else [])
        copy._order = self._order
        copy._lim = self._lim
        return copy

    def order_by(self, field: str, **kwargs) -> "MockCollRef":
        copy = MockCollRef(self._store, self._path)
        copy._filters = list(self._filters)
        copy._order = field
        copy._lim = self._lim
        return copy

    def limit(self, n: int) -> "MockCollRef":
        copy = MockCollRef(self._store, self._path)
        copy._filters = list(self._filters)
        copy._order = self._order
        copy._lim = n
        return copy

    def stream(self):
        docs = []
        for path, data in list(self._store.items()):
            if path[:len(self._path)] == self._path and len(path) == len(self._path) + 1:
                doc_id = path[-1]
                ref = MockDocRef(self._store, path)
                docs.append(MockDocument(doc_id, dict(data), ref))

        for f in self._filters:
            if f is None:
                continue
            field, op, value = f.field_path, f.op_string, f.value
            if op == "==":
                docs = [d for d in docs if d.to_dict().get(field) == value]

        if self._order == "created_at":
            docs.sort(key=lambda d: float(d.to_dict().get("created_at", 0) or 0) if isinstance(d.to_dict().get("created_at", 0), (int, float)) else 0.0)

        if self._lim:
            docs = docs[:self._lim]

        return iter(docs)


class MockStore:
    def __init__(self):
        self._data: dict[tuple, dict] = {}

    def get(self, path: tuple) -> dict | None:
        return self._data.get(path)

    def set(self, path: tuple, data: dict) -> None:
        self._data[path] = data

    def delete(self, path: tuple) -> None:
        self._data.pop(path, None)

    def items(self):
        return list(self._data.items())

    def collection(self, name: str) -> MockCollRef:
        return MockCollRef(self, (name,))

    def stream(self):
        return iter([])


# ---------------------------------------------------------------------------
# Patch google.cloud.firestore before importing our module

import sys
from unittest.mock import MagicMock

# Create a fake firestore module
_fake_firestore = MagicMock()
_fake_firestore.SERVER_TIMESTAMP = MockServerTimestamp()
_fake_firestore.FieldFilter = MockFieldFilter
_fake_firestore.ArrayUnion = lambda vals: MockArrayUnion(vals)

_google_cloud_mock = MagicMock()
_google_cloud_mock.firestore = _fake_firestore
sys.modules['google'] = MagicMock()
sys.modules['google.cloud'] = _google_cloud_mock
sys.modules['google.cloud.firestore'] = _fake_firestore
sys.modules['functions_framework'] = MagicMock()

# Now we can import our module
from firebase_function import main as fm  # noqa: E402


# ---------------------------------------------------------------------------
# Flask test app fixture

@pytest.fixture
def store():
    return MockStore()


@pytest.fixture
def app(store):
    """Flask app wrapping the matchmaker function with a mocked DB."""
    flask_app = Flask(__name__)

    mock_db = MagicMock()
    mock_db.collection.side_effect = lambda name: store.collection(name)

    @flask_app.route("/health")
    @flask_app.route("/rooms")
    @flask_app.route("/signal/<room>/<role>/send", methods=["POST"])
    @flask_app.route("/signal/<room>/<role>/recv", methods=["GET"])
    def dispatch(**kwargs):
        from flask import request
        with patch("firebase_function.main._get_db", return_value=mock_db):
            # Short poll timeout for tests
            original = fm.POLL_TIMEOUT_S
            fm.POLL_TIMEOUT_S = 1.0
            try:
                return fm._dispatch(request)
            finally:
                fm.POLL_TIMEOUT_S = original

    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Tests

def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["backend"] == "firestore"


def test_rooms_empty(client):
    resp = client.get("/rooms")
    assert resp.status_code == 200
    assert resp.get_json()["rooms"] == []


def test_send_ok(client):
    resp = client.post(
        "/signal/r1/operator/send",
        json={"type": "offer", "sdp": "abc"},
    )
    assert resp.status_code == 200


def test_send_invalid_role(client):
    resp = client.post("/signal/r1/hacker/send", json={"type": "x"})
    assert resp.status_code == 400


def test_send_invalid_json(client):
    resp = client.post(
        "/signal/r1/operator/send",
        data="notjson",
        content_type="text/plain",
    )
    assert resp.status_code == 400


def test_recv_returns_204_on_empty_room(client):
    resp = client.get("/signal/empty-room/operator/recv")
    assert resp.status_code == 204


def test_send_recv_roundtrip(client):
    sub_id = str(uuid.uuid4())
    payload = {"type": "offer", "sdp": "v=0..."}

    client.post("/signal/r2/operator/send", json=payload)
    resp = client.get("/signal/r2/operator/recv", headers={"X-Subscriber-Id": sub_id})

    assert resp.status_code == 200
    assert resp.get_json() == payload


def test_message_consumed_only_once_per_subscriber(client):
    sub_id = str(uuid.uuid4())
    client.post("/signal/r3/operator/send", json={"type": "offer"})

    resp1 = client.get("/signal/r3/operator/recv", headers={"X-Subscriber-Id": sub_id})
    assert resp1.status_code == 200

    resp2 = client.get("/signal/r3/operator/recv", headers={"X-Subscriber-Id": sub_id})
    assert resp2.status_code == 204


def test_two_subscribers_each_get_message(client):
    sub1, sub2 = str(uuid.uuid4()), str(uuid.uuid4())
    payload = {"type": "offer"}

    client.post("/signal/r4/operator/send", json=payload)

    resp1 = client.get("/signal/r4/operator/recv", headers={"X-Subscriber-Id": sub1})
    resp2 = client.get("/signal/r4/operator/recv", headers={"X-Subscriber-Id": sub2})

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.get_json() == payload
    assert resp2.get_json() == payload


def test_subscriber_id_returned_in_header(client):
    client.post("/signal/r5/robot/send", json={"type": "answer"})
    resp = client.get("/signal/r5/robot/recv")
    assert resp.status_code == 200
    assert "X-Subscriber-Id" in resp.headers


def test_full_signaling_sequence(client):
    """operator sends offer → robot recvs → robot sends answer → operator recvs."""
    op_sub = str(uuid.uuid4())
    rb_sub = str(uuid.uuid4())

    # Operator sends offer
    client.post("/signal/handshake/operator/send", json={"type": "offer", "sdp": "offer_sdp"})

    # Robot receives it
    resp = client.get("/signal/handshake/operator/recv", headers={"X-Subscriber-Id": rb_sub})
    assert resp.status_code == 200
    assert resp.get_json()["type"] == "offer"

    # Robot sends answer
    client.post("/signal/handshake/robot/send", json={"type": "answer", "sdp": "answer_sdp"})

    # Operator receives answer
    resp = client.get("/signal/handshake/robot/recv", headers={"X-Subscriber-Id": op_sub})
    assert resp.status_code == 200
    assert resp.get_json()["type"] == "answer"
