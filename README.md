# lerobot-matchmaker

WebRTC signaling server for [lerobot-remote](https://github.com/koenvanwijk/lerobot-remote).
Routes messages between operator and robot peers within named rooms via HTTP long-poll.

## Self-hosted

```bash
pip install git+https://github.com/koenvanwijk/lerobot-matchmaker
lerobot-matchmaker --host 0.0.0.0 --port 8080
```

Or with Docker / plain Python:
```bash
python -m lerobot_matchmaker --port 8080 --log-level DEBUG
```

## Cloud (Firebase Cloud Functions)

A managed, serverless alternative is deployed on Firebase Cloud Functions (europe-west1):

```
https://europe-west1-lerobot-matchmaker.cloudfunctions.net/matchmaker
```

Use it as a drop-in replacement for the self-hosted server:

```bash
lerobot-teleoperate \
  --robot.type=remote_robot \
  --robot.signaling_url=https://europe-west1-lerobot-matchmaker.cloudfunctions.net/matchmaker \
  --robot.room=my-room \
  ...
```

The cloud variant uses Firestore as the message store and supports fan-out (multiple subscribers per room). Source: [`firebase_function/main.py`](firebase_function/main.py).

To deploy your own instance:
```bash
# 1. Create a Firebase project and enable Firestore + Blaze plan
# 2. Download a service account key and set GOOGLE_APPLICATION_CREDENTIALS
cd firebase_function && python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cd .. && firebase deploy --only functions --project <your-project-id>
```

**API:** `POST /signal/{room}/{role}/send` · `GET /signal/{room}/{role}/recv` · `GET /rooms` · `GET /health`

`role` is `operator` or `robot`. See [CLAUDE.md](CLAUDE.md) for the full message sequence and open issues.
