# WebRTC P2P TCP Proxy

**Live client:** [https://abproductionit.github.io/P2P_Network/](https://abproductionit.github.io/P2P_Network/)

Browse the internet (or a LAN) through another machine over **WebRTC**. The static browser client runs on GitHub Pages; a small Python **signaling server** and **agent** run on a VPS / host you control. Application traffic goes peer-to-peer; the WebSocket server is signaling only.

## Architecture

Browser (Pages) ↔ signaling (VPS) ↔ agent host; HTTP/TCP then rides a WebRTC data channel.

```mermaid
flowchart LR
  Browser["Browser<br/>GitHub Pages client"]
  Sig["Signaling<br/>VPS · WebSocket"]
  Agent["Agent host<br/>agent.py"]
  Net["Target network<br/>HTTP / TCP"]

  Browser <-->|"wss signaling"| Sig
  Agent <-->|"wss signaling"| Sig
  Browser <-->|"WebRTC data channel"| Agent
  Agent -->|"fetch / TCP"| Net
```

## Connect flow

Enter the signaling URL and a shared username, connect, then browse through the agent.

```mermaid
sequenceDiagram
  actor User
  participant Client as Pages client
  participant Sig as Signaling VPS
  participant Agent as agent.py

  User->>Client: Server URL + username
  User->>Client: Connect
  Client->>Sig: join room
  Agent->>Sig: join same room
  Sig-->>Client: peer present
  Sig-->>Agent: peer present
  Client->>Agent: WebRTC + data channel
  User->>Client: open URL / host
  Client->>Agent: request over data channel
  Agent-->>Client: response from agent network
```

## Run on a VPS

```bash
git clone https://github.com/ABproductionIT/P2P_Network.git
cd P2P_Network
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Signaling** (defaults `0.0.0.0:9000`):

```bash
python signaling_server.py
# or: python signaling_server.py --host 0.0.0.0 --port 9000
```

Open the firewall for that port. From the **HTTPS** Pages client you need TLS in front of the process (nginx/Caddy) and a **`wss://your-domain`** URL — plain `ws://` only works when the page itself is HTTP.

**Agent** (on the machine whose network you expose; same username the client will use):

```bash
python agent.py --signaling wss://your-domain --username alice
```

**Client:** open the [live page](https://abproductionit.github.io/P2P_Network/), enter `wss://your-domain` and `alice`, click **Connect**, then browse.

Prototype only — do not expose the agent to untrusted users without auth and destination limits.
