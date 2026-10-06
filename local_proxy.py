"""Local HTTP proxy that tunnels every browser connection through the agent.

Point a browser at it and it talks to real sites end-to-end: real hostnames
in the address bar, the browser's own TLS and fingerprint, cookies on the
real domains. The agent only relays TCP bytes from its network, so sites see
a normal browser coming from the agent's IP.
"""
import asyncio
import json
import os
import uuid
from urllib.parse import urlsplit

import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration

from agent import CHUNK, ICE_SERVERS, attach_inbox, send_bytes, wait_buffered

SIGNALING = os.getenv("SIGNALING", "ws://127.0.0.1:9000")
ROOM = os.getenv("ROOM", "agent-001")
LISTEN_HOST = os.getenv("LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "9002"))
PEER_ID = "proxy-" + uuid.uuid4().hex[:8]

STRIP_HEADERS = {"proxy-connection", "connection", "keep-alive", "proxy-authorization"}

pc = None  # connected RTCPeerConnection, or None while (re)connecting


# ---------------------------------------------------------------- WebRTC

async def wait_connected(peer, timeout):
    async def poll():
        while peer.connectionState != "connected":
            if peer.connectionState in ("failed", "closed"):
                raise ConnectionError(f"WebRTC {peer.connectionState}")
            await asyncio.sleep(0.1)
    await asyncio.wait_for(poll(), timeout)


async def wait_answer(ws):
    async for raw in ws:
        message = json.loads(raw)
        if message.get("type") == "answer" and message.get("id") == PEER_ID:
            return message["sdp"]
    raise ConnectionError("signaling connection closed")


async def peer_session():
    global pc

    peer = RTCPeerConnection(RTCConfiguration(ICE_SERVERS))
    dead = asyncio.Event()

    @peer.on("connectionstatechange")
    def on_state():
        print("[WEBRTC]", peer.connectionState)
        if peer.connectionState in ("failed", "closed"):
            dead.set()

    try:
        print("[SIGNALING] connecting to", SIGNALING)
        async with websockets.connect(SIGNALING) as ws:
            await ws.send(json.dumps({"type": "join", "room": ROOM, "role": "browser"}))

            peer.createDataChannel("control")
            # aiortc gathers ICE as part of setLocalDescription.
            await peer.setLocalDescription(await peer.createOffer())
            await ws.send(json.dumps({
                "type": "offer",
                "id": PEER_ID,
                "sdp": peer.localDescription.sdp,
            }))
            print("[WEBRTC] offer sent, waiting for the agent...")

            try:
                sdp = await asyncio.wait_for(wait_answer(ws), 15)
            except asyncio.TimeoutError:
                raise ConnectionError(f'no agent answered in room "{ROOM}"') from None

        await peer.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))
        await wait_connected(peer, 20)
        pc = peer
        print(f"[READY] proxy http://{LISTEN_HOST}:{LISTEN_PORT} -> agent in room {ROOM}")
        await dead.wait()
    finally:
        if pc is peer:
            pc = None
        await peer.close()


async def maintain_peer():
    while True:
        try:
            await peer_session()
        except Exception as e:
            print("[ERROR]", e or type(e).__name__)
        print("Reconnect in 3 seconds")
        await asyncio.sleep(3)


async def open_tunnel(host, port):
    if pc is None:
        raise ConnectionError("not connected to the agent")

    channel = pc.createDataChannel(f"tcp:{host}:{port}")
    inbox = attach_inbox(channel)
    settled = asyncio.Event()
    channel.on("open", settled.set)
    channel.on("close", settled.set)
    await asyncio.wait_for(settled.wait(), 10)
    if channel.readyState != "open":
        raise ConnectionError("tunnel closed")
    return channel, inbox


# ---------------------------------------------------------------- proxy

async def relay(reader, writer, channel, inbox, initial):
    async def client_to_channel():
        try:
            if initial and not await send_bytes(channel, initial):
                return
            while data := await reader.read(CHUNK):
                if not await send_bytes(channel, data):
                    break
        except (ConnectionError, OSError):
            pass
        finally:
            # Never close from this side: aiortc frees a stream id after only
            # our half of the reset, and reusing it before the agent's half
            # crashes the agent's SCTP association. Signal EOF instead.
            await wait_buffered(channel, 0)
            if channel.readyState == "open":
                channel.send(b"")

    async def channel_to_client():
        try:
            while (message := await inbox.get()) is not None:
                writer.write(message.encode() if isinstance(message, str) else message)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()

    await asyncio.gather(client_to_channel(), channel_to_client())


def simple_response(writer, status, text):
    body = text.encode()
    writer.write(
        f"HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
    )
    writer.close()


async def handle_client(reader, writer):
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError,
            asyncio.TimeoutError, ConnectionError):
        writer.close()
        return

    lines = head.decode("latin-1").split("\r\n")
    try:
        method, target, version = lines[0].split(" ", 2)
    except ValueError:
        return simple_response(writer, "400 Bad Request", "Malformed request line")

    if method == "CONNECT":
        host, _, port = target.rpartition(":")
        initial = b""
    else:
        url = urlsplit(target)
        if url.scheme != "http" or not url.hostname:
            return simple_response(
                writer, "400 Bad Request",
                "This is a proxy. Configure your browser to use it, e.g.\n"
                f"google-chrome --proxy-server=http://{LISTEN_HOST}:{LISTEN_PORT}\n",
            )
        host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
        port = str(url.port or 80)
        path = (url.path or "/") + (f"?{url.query}" if url.query else "")
        headers = [
            line for line in lines[1:]
            if line and line.split(":", 1)[0].strip().lower() not in STRIP_HEADERS
        ]
        # One request per upstream connection, since the next request on this
        # client connection may target a different host.
        initial = (
            f"{method} {path} {version}\r\n" + "".join(h + "\r\n" for h in headers)
            + "Connection: close\r\n\r\n"
        ).encode("latin-1")

    if not port.isdigit():
        return simple_response(writer, "400 Bad Request", f"Bad target {target}")

    try:
        channel, inbox = await open_tunnel(host, int(port))
    except Exception as e:
        print(f"[TUNNEL ERROR] {host}:{port} {e or type(e).__name__}")
        return simple_response(writer, "502 Bad Gateway", f"Tunnel to {host}:{port} failed: {e}")

    print(f"[TUNNEL] {method} {host}:{port}")
    if method == "CONNECT":
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await relay(reader, writer, channel, inbox, initial)


async def main():
    server = await asyncio.start_server(handle_client, LISTEN_HOST, LISTEN_PORT)
    print(f"[PROXY] listening on http://{LISTEN_HOST}:{LISTEN_PORT}")
    async with server:
        await asyncio.gather(server.serve_forever(), maintain_peer())


if __name__ == "__main__":
    asyncio.run(main())
