#!/usr/bin/env python3
"""WebSocket signaling server for the WebRTC P2P proxy.

Signaling only — it does not serve the browser client. Host the static client
on GitHub Pages (see docs/) or any static file server, and point the page at
this server's public ws:// or wss:// URL.

This process speaks plain WebSocket (ws://) only — no TLS. Put nginx/Caddy in
front when the browser client is on HTTPS (GitHub Pages) and you need wss://.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys

import websockets


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="WebRTC P2P signaling server (plain WebSocket, no TLS).",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("HOST", "0.0.0.0"),
        help="Bind address (env HOST, default 0.0.0.0). Bind-only — clients must not use 0.0.0.0.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "9000")),
        help="Bind port (env PORT, default 9000).",
    )
    return parser.parse_args(argv)


rooms: dict[str, set] = {}


async def handler(ws):
    room_id = None
    username = None
    try:
        async for raw in ws:
            message = json.loads(raw)

            if message.get("type") == "join":
                room_id = message["room"]
                username = message.get("username") or message.get("role") or "?"
                room = rooms.setdefault(room_id, set())
                room.add(ws)
                print(f"[+] peer joined room={room_id} user={username}")
                await ws.send(json.dumps({
                    "type": "joined",
                    "room": room_id,
                    "username": message.get("username"),
                }))
                continue

            if not room_id:
                continue

            for peer in list(rooms.get(room_id, set())):
                if peer != ws:
                    try:
                        await peer.send(raw)
                    except websockets.ConnectionClosed:
                        pass
    except websockets.ConnectionClosed:
        pass
    finally:
        if room_id and room_id in rooms:
            rooms[room_id].discard(ws)
            if not rooms[room_id]:
                del rooms[room_id]
        print(f"[-] peer disconnected room={room_id} user={username}")


def guess_lan_ip() -> str | None:
    """Best-effort LAN IPv4 for connect-URL hints (not used for binding)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return None


def print_startup(host: str, port: int) -> None:
    print(f"Signaling server listening on {host}:{port}")
    print("Protocol: plain WebSocket (ws://) — no TLS in this process.")
    print("Static browser client is not served from here.")
    print()
    print("Clients connect with a real host (not 0.0.0.0):")
    print(f"  ws://127.0.0.1:{port}          # same machine")
    lan = guess_lan_ip()
    if lan:
        print(f"  ws://{lan}:{port}     # LAN (guessed)")
    else:
        print(f"  ws://<your-LAN-IP>:{port}   # other machines on your network")
    print()
    print("Do not use wss:// against this process unless TLS (nginx/Caddy) is in front.")
    print("Do not use 0.0.0.0 as a client URL — that is the bind address only.")
    print("GitHub Pages (HTTPS) cannot open ws:// (mixed content); use TLS+wss or open docs/ over http:// locally.")
    print("Open the client (docs/) and enter a ws:// URL + username matching the agent.")


async def main(argv=None):
    args = parse_args(argv)
    print_startup(args.host, args.port)
    async with websockets.serve(handler, args.host, args.port):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
