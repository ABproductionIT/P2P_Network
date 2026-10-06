#!/usr/bin/env python3
"""Remote WebRTC agent: fetches HTTP(S) and opens TCP from its network."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from contextlib import suppress
from email.utils import parsedate_to_datetime
from http.cookiejar import Cookie
from urllib.parse import urlsplit

import websockets
from curl_cffi.requests import AsyncSession

from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    RTCIceServer,
)
from aiortc.sdp import candidate_from_sdp

SIGNALING = os.getenv("SIGNALING", "ws://127.0.0.1:9000")
# ROOM and USERNAME are aliases: the browser "Username" field joins this room.
ROOM = os.getenv("ROOM") or os.getenv("USERNAME") or "agent-001"
INSECURE_TLS = os.getenv("INSECURE_TLS") == "1"
# TLS/HTTP2 fingerprint profile for Chromium-based clients (Chrome, Edge,
# Opera...); Firefox and Safari clients get their own family automatically.
IMPERSONATE = os.getenv("IMPERSONATE", "chrome")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="WebRTC P2P agent (runs on the machine whose network you expose).",
    )
    parser.add_argument(
        "--signaling",
        default=SIGNALING,
        help="Signaling WebSocket URL (env SIGNALING).",
    )
    parser.add_argument(
        "--username", "--room",
        dest="username",
        default=ROOM,
        help="Shared identity / room with the browser client (env ROOM or USERNAME).",
    )
    parser.add_argument(
        "--insecure-tls",
        action="store_true",
        default=INSECURE_TLS,
        help="Accept self-signed TLS on agent fetches (env INSECURE_TLS=1).",
    )
    parser.add_argument(
        "--impersonate",
        default=IMPERSONATE,
        help="curl_cffi browser profile (env IMPERSONATE, default chrome).",
    )
    return parser.parse_args(argv)

# aiortc advertises a 64 KiB max message size; stay well below it.
CHUNK = 16 * 1024
HIGH_WATER = 1024 * 1024

ICE_SERVERS = [RTCIceServer(urls=["stun:stun.l.google.com:19302"])]

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade",
}
# Cookies come from the per-peer cookie jar.
REQUEST_SKIP = HOP_BY_HOP | {"host", "content-length", "accept-encoding", "cookie"}
RESPONSE_SKIP = HOP_BY_HOP | {"set-cookie", "content-encoding", "content-length"}
NAVIGATION_ONLY = ("sec-fetch-user", "upgrade-insecure-requests")
CLIENT_HINTS = ("sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform")


def profile_for(user_agent):
    """Pick the impersonation family matching the real browser.

    The proxied page's JavaScript sees the real browser, and anti-bot scripts
    compare it with the request headers and TLS fingerprint.
    """
    if not user_agent:
        return IMPERSONATE
    if "Firefox/" in user_agent:
        return "firefox"
    if "Safari/" in user_agent and "Chrome/" not in user_agent and "Chromium/" not in user_agent:
        return "safari"
    return IMPERSONATE


def attach_inbox(channel):
    """Buffer messages from the moment the channel is announced.

    aiortc emits "message" synchronously, so anything that arrives before a
    listener is attached is silently dropped.
    """
    inbox = asyncio.Queue()
    channel.on("message", inbox.put_nowait)
    channel.on("close", lambda: inbox.put_nowait(None))
    return inbox


async def wait_buffered(channel, limit):
    while channel.readyState == "open" and channel.bufferedAmount > limit:
        await asyncio.sleep(0.01)


async def send_bytes(channel, data):
    for pos in range(0, len(data), CHUNK):
        await wait_buffered(channel, HIGH_WATER)
        if channel.readyState != "open":
            return False
        channel.send(data[pos:pos + CHUNK])
    return True


async def close_when_flushed(channel):
    # A stream reset can overtake data still queued in aiortc.
    await wait_buffered(channel, 0)
    if channel.readyState == "open":
        channel.close()


async def add_remote_candidate(pc, message):
    try:
        candidate = candidate_from_sdp(message["candidate"].split(":", 1)[1])
        candidate.sdpMid = message.get("sdpMid")
        candidate.sdpMLineIndex = message.get("sdpMLineIndex")
        await pc.addIceCandidate(candidate)
        print("[WEBRTC] remote candidate", candidate.ip, candidate.port, candidate.type)
    except Exception as e:
        print("[WEBRTC] bad remote candidate:", e)


# ---------------------------------------------------------------- raw TCP

async def handle_tcp_channel(channel, inbox):
    try:
        host, port = channel.label.removeprefix("tcp:").rsplit(":", 1)
        port = int(port)
    except ValueError:
        channel.close()
        return

    host = host.strip("[]")
    print(f"[CONNECT] {host}:{port}")

    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 15)
    except Exception as e:
        print("[CONNECT ERROR]", host, port, e)
        channel.close()
        return

    print(f"[CONNECTED] {host}:{port}")

    async def channel_to_tcp():
        try:
            while (message := await inbox.get()) is not None:
                if message == b"":
                    break  # EOF from local_proxy; the agent closes the channel
                writer.write(message.encode() if isinstance(message, str) else message)
                await writer.drain()
        except Exception as e:
            print("[TCP WRITE ERROR]", e)
        finally:
            writer.close()

    async def tcp_to_channel():
        try:
            while data := await reader.read(CHUNK):
                if not await send_bytes(channel, data):
                    break
        except Exception as e:
            print("[TCP READ ERROR]", e)
        finally:
            await close_when_flushed(channel)

    await asyncio.gather(channel_to_tcp(), tcp_to_channel())
    print(f"[CLOSE] {host}:{port}")


# ---------------------------------------------------------------- cookies
# Proxied pages run on the client's origin, so cookies they set through
# document.cookie would never reach the target site. The page forwards those
# writes here, and gets the site's script-visible cookies back.

def cookie_matches(cookie, host, path, secure):
    domain = cookie.domain.lstrip(".")
    if cookie.domain_specified or cookie.domain.startswith("."):
        if host != domain and not host.endswith("." + domain):
            return False
    elif host != cookie.domain:
        return False
    cookie_path = cookie.path or "/"
    if path != cookie_path and not path.startswith(cookie_path.rstrip("/") + "/"):
        return False
    return not (cookie.secure and not secure) and not cookie.is_expired()


def is_http_only(cookie):
    return cookie.get_nonstandard_attr("http_only") == "True"


def document_cookies(jar, url):
    """The cookie string document.cookie would return on this URL."""
    u = urlsplit(url)
    host, path, secure = (u.hostname or "").lower(), u.path or "/", u.scheme == "https"
    visible = [
        c for c in jar
        if cookie_matches(c, host, path, secure) and not is_http_only(c)
    ]
    visible.sort(key=lambda c: len(c.path or "/"), reverse=True)
    return "; ".join(f"{c.name}={c.value}" for c in visible)


def set_document_cookie(jar, url, raw):
    """Apply a document.cookie assignment made by a page at url."""
    u = urlsplit(url)
    host = (u.hostname or "").lower()
    pair, *attr_parts = [p.strip() for p in raw.split(";")]
    name, sep, value = pair.partition("=")
    name = name.strip()
    if not sep or not name:
        return

    attrs = {}
    for part in attr_parts:
        key, _, val = part.partition("=")
        attrs[key.strip().lower()] = val.strip()

    domain = attrs.get("domain", "").lstrip(".").lower()
    if domain and host != domain and not host.endswith("." + domain):
        return
    cookie_domain = "." + domain if domain else host
    path = attrs.get("path") or (u.path.rsplit("/", 1)[0] or "/")

    if any(c.name == name and is_http_only(c) and cookie_matches(c, host, path, True) for c in jar):
        return  # scripts cannot overwrite HttpOnly cookies

    expires = None
    try:
        if "max-age" in attrs:
            expires = int(time.time()) + int(attrs["max-age"])
        elif "expires" in attrs:
            expires = int(parsedate_to_datetime(attrs["expires"]).timestamp())
    except (ValueError, TypeError):
        pass

    if expires is not None and expires <= time.time():
        with suppress(KeyError):
            jar.clear(cookie_domain, path, name)
        return

    jar.set_cookie(Cookie(
        version=0, name=name, value=value.strip(), port=None, port_specified=False,
        domain=cookie_domain, domain_specified=bool(domain), domain_initial_dot=bool(domain),
        path=path, path_specified=True, secure="secure" in attrs,
        expires=expires, discard=expires is None, comment=None, comment_url=None,
        rest={}, rfc2109=False,
    ))


# ---------------------------------------------------------------- HTTP

async def handle_http_channel(channel, inbox, session):
    resp = None

    async def fail(message):
        print("[HTTP ERROR]", message)
        if channel.readyState == "open":
            channel.send(json.dumps({"type": "error", "message": message}))
            await close_when_flushed(channel)

    try:
        first = await inbox.get()
        if not isinstance(first, str):
            return await fail("expected JSON request header")
        req = json.loads(first)

        body = bytearray()
        while len(body) < req.get("bodyLength", 0):
            chunk = await inbox.get()
            if chunk is None:
                return
            body += chunk.encode() if isinstance(chunk, str) else chunk

        url = req["url"]
        if not url.startswith(("http://", "https://")):
            return await fail(f"unsupported URL: {url}")

        for page_url, raw_cookie in req.get("cookieWrites", []):
            set_document_cookie(session.cookies.jar, page_url, raw_cookie)
        headers = {
            name.lower(): value for name, value in req.get("headers", [])
            if name.lower() not in REQUEST_SKIP
        }
        # The impersonation profile adds these for every request; real
        # browsers only send them on navigations. None disables a header.
        for name in NAVIGATION_ONLY:
            headers.setdefault(name, None)
        user_agent = headers.get("user-agent")
        if user_agent:
            # Use the real browser's identity; drop profile hints it lacks
            # (e.g. Firefox sends no client hints).
            for name in CLIENT_HINTS:
                headers.setdefault(name, None)

        method = req.get("method", "GET")
        print(f"[HTTP] {method} {url}")
        try:
            resp = await session.request(
                method, url,
                headers=headers,
                content=bytes(body) if body else None,
                allow_redirects=False,
                stream=True,
                impersonate=profile_for(user_agent),
            )
        except Exception as e:
            return await fail(f"{url}: {e}")

        channel.send(json.dumps({
            "type": "head",
            "status": resp.status_code,
            "statusText": resp.reason or "",
            "headers": [
                [name, value] for name, value in resp.headers.multi_items()
                if value is not None and name.lower() not in RESPONSE_SKIP
            ],
            "cookies": document_cookies(session.cookies.jar, url),
        }))

        async for data in resp.aiter_content():
            if not await send_bytes(channel, data):
                return

        if channel.readyState == "open":
            channel.send(json.dumps({"type": "end"}))
            await close_when_flushed(channel)
    except Exception as e:
        await fail(f"{type(e).__name__}: {e}")
    finally:
        if resp is not None:
            if resp.quit_now:
                resp.quit_now.set()
            await resp.aclose()


# ---------------------------------------------------------------- peers

peers = {}


class Peer:
    """One connected client (browser page or local_proxy.py)."""

    def __init__(self, peer_id):
        self.id = peer_id
        self.closed = False
        self.pc = RTCPeerConnection(RTCConfiguration(ICE_SERVERS))
        # Per-client cookie jar and connection pool.
        self.http = AsyncSession(
            impersonate=IMPERSONATE,
            verify=not INSECURE_TLS,
            timeout=(15, 60),
        )
        self.pc.on("datachannel", self.on_datachannel)
        self.pc.on("connectionstatechange", self.on_state)

    def on_datachannel(self, channel):
        print(f"[{self.id}] [DATA CHANNEL] {channel.label}")
        inbox = attach_inbox(channel)

        if channel.label == "http":
            asyncio.create_task(handle_http_channel(channel, inbox, self.http))
        elif channel.label.startswith("tcp:"):
            asyncio.create_task(handle_tcp_channel(channel, inbox))
        # Anything else (the "control" channel) is just kept open.

    async def on_state(self):
        print(f"[{self.id}] [WEBRTC] {self.pc.connectionState}")
        if self.pc.connectionState in ("failed", "closed"):
            await self.close()

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if peers.get(self.id) is self:
            del peers[self.id]
        await self.pc.close()
        await self.http.close()


async def handle_offer(ws, message):
    peer_id = message.get("id") or "default"
    print(f"[{peer_id}] [WEBRTC] offer received")

    old = peers.get(peer_id)
    if old:
        await old.close()

    peer = peers[peer_id] = Peer(peer_id)
    await peer.pc.setRemoteDescription(
        RTCSessionDescription(sdp=message["sdp"], type="offer")
    )
    await peer.pc.setLocalDescription(await peer.pc.createAnswer())

    # aiortc gathers ICE as part of setLocalDescription.
    await ws.send(json.dumps({
        "type": "answer",
        "id": peer_id,
        "sdp": peer.pc.localDescription.sdp,
    }))
    print(f"[{peer_id}] [WEBRTC] answer sent")


async def run_agent(signaling: str, username: str):
    while True:
        try:
            print("[SIGNALING] connecting to", signaling, "as", username)
            async with websockets.connect(signaling) as ws:
                print("[SIGNALING] connected")

                await ws.send(json.dumps({
                    "type": "join",
                    "room": username,
                    "role": "agent",
                    "username": username,
                }))

                async for raw in ws:
                    message = json.loads(raw)
                    kind = message.get("type")

                    if kind == "offer":
                        await handle_offer(ws, message)
                    elif kind == "candidate":
                        peer = peers.get(message.get("id") or "default")
                        if peer and peer.pc.remoteDescription:
                            await add_remote_candidate(peer.pc, message)

        except Exception as e:
            print("[ERROR]", e)
            print("Reconnect in 3 seconds")
            await asyncio.sleep(3)


def main(argv=None):
    global INSECURE_TLS, IMPERSONATE
    args = parse_args(argv)
    INSECURE_TLS = bool(args.insecure_tls)
    IMPERSONATE = args.impersonate
    asyncio.run(run_agent(args.signaling, args.username))


if __name__ == "__main__":
    main()
