# lerobot-matchmaker

WebRTC signaling and matchmaking server for lerobot-remote.
Routes messages between an operator (`lerobot-robot-remote`) and a robot
(`lerobot-teleoperator-remote`) within a named room via HTTP long-poll.

## Package layout

```
src/lerobot_matchmaker/
  server.py      # aiohttp app + route handlers
  room.py        # Room state, per-role asyncio queues, RoomRegistry
  __main__.py    # CLI entry point: lerobot-matchmaker / python -m lerobot_matchmaker
```

## HTTP API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/signal/{room}/{role}/send` | Store a message sent by `{role}` |
| GET  | `/signal/{room}/{role}/recv` | Long-poll: wait for a message sent by `{role}` (25s timeout → 204) |
| GET  | `/rooms` | List active rooms (debug) |
| GET  | `/health` | Health check |

`role` must be `"operator"` or `"robot"`.

**Routing:** the receiver polls the *sender's* queue.
- Operator reads messages sent by robot: `GET /signal/{room}/robot/recv`
- Robot reads messages sent by operator: `GET /signal/{room}/operator/recv`

## Message sequence (one room)

```
operator POST /signal/arm-1/operator/send  {"type": "capabilities", "teleop_modes": [...]}
robot    GET  /signal/arm-1/operator/recv  → receives capabilities

robot    POST /signal/arm-1/robot/send     {"type": "capabilities", "robot_modes": [...]}
operator GET  /signal/arm-1/robot/recv     → receives capabilities

operator POST /signal/arm-1/operator/send  {"type": "mode_agreed", "teleop_mode": {...}, "robot_mode": {...}}
robot    GET  /signal/arm-1/operator/recv  → receives agreed modes

operator POST /signal/arm-1/operator/send  {"type": "offer", "sdp": "..."}
robot    GET  /signal/arm-1/operator/recv  → receives SDP offer

robot    POST /signal/arm-1/robot/send     {"type": "answer", "sdp": "..."}
operator GET  /signal/arm-1/robot/recv     → receives SDP answer
```

After the SDP exchange, communication moves to the WebRTC DataChannel
and the matchmaker is no longer involved.

## Key classes

### `RoomRegistry` (`room.py`)
- `get_or_create(name)` → `Room` — creates room on first access, never deleted (see open issues)
- `list_rooms()` → `list[str]`

### `Room` (`room.py`)
- Two `asyncio.Queue` instances: `queues["operator"]` and `queues["robot"]`
- `put(sender_role, message)` — enqueue a message
- `get(sender_role, timeout=25.0)` → `dict | None` — long-poll, returns `None` on timeout

### `server.py`
- `create_app()` → `aiohttp.web.Application` — wire up routes and registry
- `handle_send` — POST handler; validates role, parses JSON, enqueues
- `handle_recv` — GET handler; long-polls queue, returns 200+JSON or 204

## Running

```bash
pip install lerobot-matchmaker
lerobot-matchmaker --host 0.0.0.0 --port 8080 --log-level DEBUG

# or
python -m lerobot_matchmaker --port 8080
```

## Install

```bash
pip install git+https://github.com/koenvanwijk/lerobot-matchmaker
```

## Open issues

- ~~**#1 Room cleanup**~~ — **Fixed**: rooms expire after `ROOM_TTL_SECONDS` (300s) of inactivity via `_cleanup_loop()`.

- **#2 No authentication** *(open)* — any client can join any room by name. Minimum viable: a `?token=` query param validated against an env-var allowlist.

- ~~**#3 Single-consumer queues**~~ — **Fixed**: fan-out queues per subscriber_id; each subscriber gets their own copy. Multiple operators can poll the same room/role independently.

- **#4 In-memory only** *(open)* — all state is lost on server restart. Reconnecting peers must redo the full handshake. The Firebase Cloud Functions variant (`firebase_function/`) uses Firestore as persistent backend.

## Related repos

- `koenvanwijk/lerobot-remote` — `SignalingClient` that connects to this server
- `koenvanwijk/lerobot-action-space` — `ActionMode` definitions exchanged during signaling
