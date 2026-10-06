# WebRTC P2P TCP Proxy Prototype

Components:

- `signaling_server.py` — WebSocket signaling.
- `agent.py` — remote WebRTC agent that fetches HTTP(S) pages and opens TCP connections from its network.
- `client.html` — "browser in a browser": address bar, back/forward/reload and an iframe whose traffic goes through the agent.
- `sw.js` — service worker that routes every request of the embedded page over WebRTC.
- `local_proxy.py` — local HTTP/HTTPS proxy for a real browser; every connection is tunneled through the agent.
- `requirements.txt` — Python dependencies.

Two ways to browse through the agent:

| | `local_proxy.py` + real Chrome | `client.html` (browser in a browser) |
|---|---|---|
| Address bar / `location` | real domain | `127.0.0.1/__p/...` (shown as real in the toolbar) |
| TLS | end-to-end, the browser's own | terminated on the agent (Chrome fingerprint via `curl_cffi`) |
| Captchas, anti-bot checks (Google search, reCAPTCHA, Turnstile) | work like in a normal browser | in-page checks see the proxy and may block |
| Install on the client | Python + this script | nothing, just open a page |

Use `local_proxy.py` when sites must not notice the proxy (captchas, logins, anti-bot checks).

## 1. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Start signaling

```bash
python signaling_server.py
```

Default port: `9000` (`PORT=...` to change).

## 3. Start agent

On the computer whose network you want to access:

```bash
SIGNALING=ws://SIGNALING_SERVER_IP:9000 ROOM=agent-001 python agent.py
```

For a same-machine test:

```bash
SIGNALING=ws://127.0.0.1:9000 ROOM=agent-001 python agent.py
```

## 4a. Real browser through `local_proxy.py` (recommended)

On your computer:

```bash
SIGNALING=ws://SIGNALING_SERVER_IP:9000 ROOM=agent-001 python local_proxy.py
```

Wait for `[READY]`, then start Chrome with a separate profile that uses the proxy:

```bash
google-chrome --user-data-dir="$HOME/.p2p-chrome" \
  --proxy-server=http://127.0.0.1:9002 \
  --force-webrtc-ip-handling-policy=disable_non_proxied_udp
```

Every connection (including DNS resolution) is made from the agent's network; sites see a normal
Chrome with real hostnames, coming from the agent's IP. `LISTEN_PORT` changes the proxy port.

## 4b. Browser in a browser (`client.html`)

The signaling server also serves the client: open `http://127.0.0.1:9000/` (service workers need
`localhost`/`127.0.0.1` or HTTPS; on another machine put it behind HTTPS). The signaling URL is
pre-filled from the address you opened.

Set the room, click **Connect**, then type an address reachable from the agent
(e.g. `192.168.1.1`, `intranet.local:8080/admin`, `https://example.com`) and press Enter.

How it works:

- The page is shown in an iframe at `/__p/<scheme>/<host:port>/<path>`.
- `sw.js` intercepts every request of that iframe (HTML, CSS, JS, images, forms, XHR/fetch) and hands it to `client.html`.
- `client.html` opens an `http` DataChannel per request; the agent performs the request from its network
  (HTTP and HTTPS, cookies kept per session, redirects passed back to the browser) and streams the response.
- The **Raw TCP test** panel (gear button) still opens a plain `tcp:<host>:<port>` tunnel.

The agent fetches with `curl_cffi` impersonating Chrome (TLS + HTTP/2 fingerprint and default headers),
and the service worker sends `Referer`, `Origin` and `Sec-Fetch-*` as if the page were opened on its
real domain. `IMPERSONATE=chrome146` (or another `curl_cffi` profile) selects the browser profile.
Cookies the page sets through `document.cookie` are forwarded to the agent's cookie jar, and the
site's script-visible cookies are readable by the page, so JS-set session cookies work.

Google search is an exception: its BotGuard challenge runs inside the page, sees the proxy origin and
the service worker, and Google answers with `/sorry` (captcha). Use `local_proxy.py` for Google.

Set `INSECURE_TLS=1` on the agent to accept self-signed certificates (routers, NAS, etc.).

Limitations: WebSockets opened by the proxied page and cross-origin iframes inside it bypass the tunnel;
proxied pages run on the client's origin, so only open sites you trust.

## Important

This is a development prototype, not a production VPN.

The current agent accepts arbitrary TCP destinations supplied by a connected peer. Do not expose it to untrusted users or the public Internet without authentication and an ACL.

For production use add:

- authentication / per-agent credentials
- destination subnet and port ACLs
- TLS/WSS for signaling
- TURN fallback
- connection/session limits
- logging and auditing
- multiplexing instead of one DataChannel per TCP stream

The WebSocket server handles signaling only. Application traffic is intended to flow over WebRTC.
