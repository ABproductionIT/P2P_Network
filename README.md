# WebRTC P2P TCP Proxy

Browser client on **GitHub Pages** (static). Python signaling / agent / local proxy on **any VPS** you control. The HTML is not served by the Python process.

| Piece | Where it runs | Role |
|---|---|---|
| `docs/` (`index.html`, `sw.js`) | GitHub Pages (or any static host) | Browser-in-a-browser UI |
| `signaling_server.py` | Public VPS | WebSocket signaling only |
| `agent.py` | Machine whose network you expose | WebRTC peer that fetches / opens TCP |
| `local_proxy.py` | Your laptop (optional) | Real Chrome via local HTTP proxy |
| `requirements.txt` | VPS / agent host | Python deps |

Two ways to browse through the agent:

| | `local_proxy.py` + real Chrome | GitHub Pages client |
|---|---|---|
| Address bar / `location` | real domain | Pages origin + `/__p/...` (shown as real in the toolbar) |
| TLS | end-to-end, the browser's own | terminated on the agent (`curl_cffi`) |
| Captchas / anti-bot | work like a normal browser | in-page checks may block |
| Client install | Python + script | open the Pages URL |

## 1. Install Python deps (VPS and/or agent host)

```bash
git clone https://github.com/ABproductionIT/P2P_Network.git
cd P2P_Network
git checkout develop
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Run the signaling server on a VPS

Entrypoint: `signaling_server.py`. Bind with flags or env:

```bash
# defaults: HOST=0.0.0.0 PORT=9000
python signaling_server.py

# explicit
python signaling_server.py --host 0.0.0.0 --port 9000

# env form
HOST=0.0.0.0 PORT=9000 python signaling_server.py
```

Open the firewall for that port. From a browser on **HTTPS** GitHub Pages you must terminate TLS in front of the process (nginx/Caddy) and expose **`wss://your-vps:443`** (or another TLS port). Plain `ws://` only works when the client page itself is HTTP (e.g. local static server).

Example nginx stream/location sketch (TLS at 443, proxy to local 9000):

```nginx
location / {
    proxy_pass http://127.0.0.1:9000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
}
```

Then the client server URL is `wss://your.domain`.

## 3. Start the agent

On the computer whose network you want to reach (same username the browser will type):

```bash
python agent.py --signaling wss://your.domain --username alice

# env form (ROOM and USERNAME are aliases)
SIGNALING=wss://your.domain ROOM=alice python agent.py
```

Same-machine smoke test:

```bash
python signaling_server.py --port 9000
python agent.py --signaling ws://127.0.0.1:9000 --username agent-001
```

Useful flags / env:

| Flag | Env | Meaning |
|---|---|---|
| `--signaling` | `SIGNALING` | Signaling WebSocket URL |
| `--username` / `--room` | `ROOM` or `USERNAME` | Shared identity with the browser |
| `--insecure-tls` | `INSECURE_TLS=1` | Accept self-signed upstream certs |
| `--impersonate` | `IMPERSONATE` | `curl_cffi` profile (default `chrome`) |

## 4. Enable GitHub Pages (static client)

The client lives under **`docs/`** so Pages can publish it without coupling to Python.

### One-time clicks in the GitHub UI

1. Open [ABproductionIT/P2P_Network](https://github.com/ABproductionIT/P2P_Network) → **Settings** → **Pages**.
2. Under **Build and deployment** → **Source**, choose **Deploy from a branch**.
3. **Branch**: `develop` · **Folder**: `/docs` → **Save**.
4. Wait a minute for the green “Your site is live at …” banner.

Expected URL:

**https://abproductionit.github.io/P2P_Network/**

(`index.html` + `sw.js` from `docs/`; `.nojekyll` is included so Jekyll does not strip anything.)

If your org blocks Pages or the Pages API, an admin must allow GitHub Pages for the org/repo, then repeat the steps above.

### Local static preview (optional)

```bash
python3 -m http.server 4173 --directory docs
# open http://127.0.0.1:4173/
```

## 5. Connect from the client

1. Open the Pages URL (or local static preview).
2. In **Connect**:
   - **Server URL** — public signaling endpoint, e.g. `wss://your.domain` or `ws://VPS_IP:9000`.
   - **Username** — same string as the agent’s `--username` / `ROOM`.
3. Click **Connect**.
4. Type an address reachable from the agent (e.g. `192.168.1.1`, `https://example.com`) and press Enter.

Values are remembered in `localStorage`. On HTTPS Pages, `http://` / `ws://` inputs are upgraded to `wss://`.

How the Pages client works:

- The page is shown in an iframe under `/__p/<scheme>/<host:port>/<path>`.
- `sw.js` intercepts every request of that iframe and hands it to the page.
- The page opens an `http` DataChannel per request; the agent performs it from its network.
- **Raw TCP test** (gear panel) still opens a `tcp:<host>:<port>` tunnel.

## 6. Optional: real browser via `local_proxy.py`

```bash
python local_proxy.py --signaling wss://your.domain --username alice
# LISTEN_HOST / LISTEN_PORT or --listen-host / --listen-port
```

Wait for `[READY]`, then:

```bash
google-chrome --user-data-dir="$HOME/.p2p-chrome" \
  --proxy-server=http://127.0.0.1:9002 \
  --force-webrtc-ip-handling-policy=disable_non_proxied_udp
```

## Important

This is a development prototype, not a production VPN.

The agent accepts arbitrary TCP destinations from a connected peer. Do not expose it to untrusted users or the public Internet without authentication and an ACL.

For production use add authentication, destination ACLs, WSS, TURN fallback, session limits, auditing, and multiplexing instead of one DataChannel per stream.

Application traffic is intended to flow over WebRTC; the WebSocket server is signaling only.
