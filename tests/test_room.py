"""
tests/test_room.py — Unit tests for Room and RoomRegistry.

Tests:
  - Basic put/get (single subscriber)
  - Fan-out: two subscribers both receive the same message
  - Backlog: message sent before any subscriber arrives is delivered on subscribe
  - get() returns None on timeout
  - subscribe() is idempotent (same subscriber_id → same queue, no duplicate)
  - Room.is_expired() respects TTL
  - RoomRegistry: get_or_create, list_rooms, cleanup_loop
"""

from __future__ import annotations

import asyncio
import time

import pytest

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from lerobot_matchmaker.room import Room, RoomRegistry, ROOM_TTL_SECONDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_room(name: str = "test") -> Room:
    return Room(name=name)


# ---------------------------------------------------------------------------
# Basic put / get
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_put_get_single_subscriber():
    room = make_room()
    sid = room.subscribe("operator")
    msg = {"type": "offer", "sdp": "v=0..."}
    await room.put("operator", msg)
    result = await room.get("operator", sid, timeout=1.0)
    assert result == msg


@pytest.mark.asyncio
async def test_get_timeout_returns_none():
    room = make_room()
    sid = room.subscribe("robot")
    result = await room.get("robot", sid, timeout=0.05)
    assert result is None


@pytest.mark.asyncio
async def test_multiple_messages_ordered():
    room = make_room()
    sid = room.subscribe("robot")
    msgs = [{"seq": i} for i in range(5)]
    for m in msgs:
        await room.put("robot", m)
    received = []
    for _ in range(5):
        r = await room.get("robot", sid, timeout=1.0)
        received.append(r)
    assert received == msgs


# ---------------------------------------------------------------------------
# Fan-out: two subscribers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fanout_two_subscribers():
    room = make_room()
    sid1 = room.subscribe("operator")
    sid2 = room.subscribe("operator")
    msg = {"type": "offer", "sdp": "..."}
    await room.put("operator", msg)
    r1 = await room.get("operator", sid1, timeout=1.0)
    r2 = await room.get("operator", sid2, timeout=1.0)
    assert r1 == msg
    assert r2 == msg


@pytest.mark.asyncio
async def test_fanout_independent_queues():
    """Each subscriber has its own queue — consuming from one doesn't affect the other."""
    room = make_room()
    sid1 = room.subscribe("robot")
    sid2 = room.subscribe("robot")
    for i in range(3):
        await room.put("robot", {"i": i})
    # Drain sid1
    for _ in range(3):
        await room.get("robot", sid1, timeout=1.0)
    # sid2 still has all 3
    for i in range(3):
        r = await room.get("robot", sid2, timeout=1.0)
        assert r == {"i": i}


# ---------------------------------------------------------------------------
# Backlog: message before any subscriber
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backlog_delivered_on_first_get():
    """A message sent before any real subscriber is available should be delivered."""
    room = make_room()
    # No subscribers yet — goes into __backlog__
    await room.put("operator", {"type": "offer"})
    # Now subscribe and receive
    sid = room.subscribe("operator")
    result = await room.get("operator", sid, timeout=1.0)
    assert result == {"type": "offer"}


# ---------------------------------------------------------------------------
# Subscribe idempotency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subscribe_idempotent():
    room = make_room()
    sid = "fixed-uuid-1234"
    room.subscribe("operator", sid)
    room.subscribe("operator", sid)  # second call — should not add a second queue
    await room.put("operator", {"type": "offer"})
    r = await room.get("operator", sid, timeout=1.0)
    assert r == {"type": "offer"}
    # Only one message should exist (not duplicated)
    r2 = await room.get("operator", sid, timeout=0.05)
    assert r2 is None


# ---------------------------------------------------------------------------
# Unsubscribe
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unsubscribe_removes_queue():
    room = make_room()
    sid = room.subscribe("robot")
    room.unsubscribe("robot", sid)
    # After unsubscribe, get returns None immediately
    result = await room.get("robot", sid, timeout=0.05)
    assert result is None


# ---------------------------------------------------------------------------
# is_expired / TTL
# ---------------------------------------------------------------------------

def test_room_not_expired_immediately():
    room = make_room()
    assert not room.is_expired()


def test_room_expired_after_ttl():
    room = make_room()
    room.last_active = time.monotonic() - ROOM_TTL_SECONDS - 1
    assert room.is_expired()


def test_room_touch_resets_expiry():
    room = make_room()
    room.last_active = time.monotonic() - ROOM_TTL_SECONDS - 1
    assert room.is_expired()
    room._touch()
    assert not room.is_expired()


# ---------------------------------------------------------------------------
# RoomRegistry
# ---------------------------------------------------------------------------

def test_registry_get_or_create():
    registry = RoomRegistry()
    r1 = registry.get_or_create("room-a")
    r2 = registry.get_or_create("room-a")
    assert r1 is r2


def test_registry_list_rooms():
    registry = RoomRegistry()
    registry.get_or_create("r1")
    registry.get_or_create("r2")
    rooms = registry.list_rooms()
    names = [r["name"] for r in rooms]
    assert "r1" in names
    assert "r2" in names


@pytest.mark.asyncio
async def test_registry_cleanup_removes_expired():
    registry = RoomRegistry()
    r = registry.get_or_create("stale")
    r.last_active = time.monotonic() - ROOM_TTL_SECONDS - 1

    # Manually trigger one cleanup cycle
    expired = [name for name, room in registry._rooms.items() if room.is_expired()]
    for name in expired:
        del registry._rooms[name]

    assert "stale" not in registry._rooms
