# P2P Net — WebRTC chat

Python signaling server + browser clients. Clients join a room and exchange text through reliable, ordered **WebRTC DataChannels**. The signaling server relays only addressed SDP/ICE messages, never chat messages. No proxy agent, database, build step, cPanel or Passenger is needed.

## Quick start (Python 3.10+)

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python signaling_server.py --host 0.0.0.0 --port 9000
```

Alternatively, with uv: `./scripts/setup.sh`, then `./scripts/run-signaling.sh`.

1. Open **http://localhost:9000** in two browsers or tabs.
2. Enter a name and join. Click **Скопировать приглашение**.
3. Open the invitation in the second browser, enter another name, join.
4. Wait for **чат подключён**, then send a message. Enter sends; Shift+Enter adds a line break.

For another device, use your server's real IP/domain instead of localhost. When sharing a link, both the page address and signaling address must be reachable by the recipient. For internet deployment use HTTPS/WSS below.

## VPS / Docker / HTTPS

The server serves the client at `/`, JavaScript at `/chat.js`, health at `/health`, WebSocket at `/ws`.

```bash
cp .env.example .env
# Edit .env: set your allowed origin, TURN details; remove TURN variables if unused.
docker compose up -d --build
```

Compose publishes HTTP only on **127.0.0.1:9000**. Install Caddy on the host, replace the domain in `Caddyfile.example`, and use it as your Caddy configuration. Point DNS to the VPS and allow inbound TCP 80/443. Caddy terminates HTTPS and proxies WebSockets. Open `https://your-domain`; the client defaults to `wss://your-domain/ws`.

Without Docker, run the Python command above behind the same reverse proxy. Set environment variables with your process manager or shell: Python does **not** load `.env` automatically.

Use **one server process/replica**: room state is in memory. Horizontal scaling requires shared room presence/routing, which is not implemented.

## STUN / TURN

The client receives ICE configuration when joining. Google STUN is used by default. STUN alone cannot connect every pair of networks. For reliable internet use, provide coturn or a compatible TURN service supporting REST/HMAC credentials:

```bash
export TURN_URLS='turn:turn.example.com:3478?transport=udp,turn:turn.example.com:3478?transport=tcp'
export TURN_SECRET='your-long-random-secret'
export ALLOWED_ORIGINS='https://chat.example.com'
.venv/bin/python signaling_server.py
```

Configure coturn with `use-auth-secret`, matching `static-auth-secret`, a realm, public IP/external-IP mapping if applicable, and a relay port range. Open 3478 UDP/TCP and that relay range. For restrictive networks, additionally configure TURN TLS with a valid certificate and add its `turns:` URL. TURN is a separate service and is **not included** in Compose.

The server issues credentials valid for one hour; the shared TURN secret is never sent to the browser. Rejoin to obtain fresh credentials if an ICE restart is needed after expiry. TURN can relay WebRTC traffic, but does not terminate DataChannel encryption. Actual connectivity across external NATs must be tested on the deployment network.

## Behavior and boundaries

- Up to 8 clients per room, full mesh: each pair has its own connection.
- Random 128-bit invitation room IDs; anyone with the link can enter. There are no accounts or verified identities. Names are self-selected.
- Room links use the URL fragment. The room ID is still visible to the signaling service when joining.
- Reliable delivery on each open DataChannel; no offline delivery, receipt acknowledgments or persistence. The UI reports partial sends. No global message ordering across senders.
- History stays in the tab (maximum 500 rendered entries), disappears on refresh, and is not synchronized to newcomers.
- Signaling reconnect uses exponential backoff and rebuilds membership/connections. Existing channels can carry messages during the retry delay. Failed peer connections trigger up to three ICE restart attempts from their initiator.
- Text limit 4,000 characters; bounded channel send buffers. Server: max 64 KiB signal, rate limit, max 1,000 sockets, same-room routing, server-assigned sender IDs.
- `ALLOWED_ORIGINS` is an optional comma-separated browser Origin allowlist; it is not authentication. For public production deployment add access control and IP-level connection limits at your edge, especially to prevent abuse of TURN credentials.
- HTML messages are displayed as text, not rendered. No external frontend scripts.
- GitHub Pages can serve `docs/`: enter the external **wss://…/ws** endpoint. Add the Pages origin to `ALLOWED_ORIGINS`. The former proxy service worker is unregistered by the new client.

## Signaling protocol

```json
{"type":"join","room":"random-room-id","name":"Alice"}
{"type":"signal","to":"server-assigned-peer-id","data":{"description":{"type":"offer","sdp":"..."}}}
{"type":"signal","to":"server-assigned-peer-id","data":{"candidate":{"candidate":"...","sdpMid":"0","sdpMLineIndex":0}}}
```

Server messages: `joined` (own ID, existing peers, ICE servers), `peer-joined`, `peer-left`, `signal` (trusted `from` ID), `error`. Newcomers initiate offers to existing peers, preventing simultaneous initial offers. Browser queues ICE candidates until remote SDP is set and serializes signaling per peer.

## Tests

```bash
python3 -m unittest discover -s tests -v
node --check docs/chat.js
# Optional full browser test: install Playwright and its Chromium first
node tests/browser.cjs
```

Old proxy source is retained in `legacy/` for reference; it is not used and is not compatible with the new signaling protocol. Its historical dependencies were aiortc, websockets and curl_cffi.
