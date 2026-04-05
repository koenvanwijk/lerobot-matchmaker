# lerobot-matchmaker

WebRTC signaling server for [lerobot-remote](https://github.com/koenvanwijk/lerobot-remote).
Routes messages between operator and robot peers within named rooms via HTTP long-poll.

```bash
pip install git+https://github.com/koenvanwijk/lerobot-matchmaker
lerobot-matchmaker --host 0.0.0.0 --port 8080
```

Or with Docker / plain Python:
```bash
python -m lerobot_matchmaker --port 8080 --log-level DEBUG
```

**API:** `POST /signal/{room}/{role}/send` · `GET /signal/{room}/{role}/recv` · `GET /health`

`role` is `operator` or `robot`. See [CLAUDE.md](CLAUDE.md) for the full message sequence and open issues.
