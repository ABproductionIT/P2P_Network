#!/usr/bin/env python3
"""WebSocket signaling server for the WebRTC P2P proxy.

Signaling only — it does not serve the browser client. Host the static client
on GitHub Pages (see docs/) or any static file server, and point the page at
this server's public ws:// or wss:// URL.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import websockets


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="WebRTC P2P signaling server (WebSocket only).",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("HOST", "0.0.0.0"),
        help="Bind address (env HOST, default 0.0.0.0).",
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


async def main(argv=None):
    args = parse_args(argv)
    print(f"Signaling server: ws://{args.host}:{args.port}")
    print("Static browser client is not served from here.")
    print("Open the GitHub Pages client (docs/) and enter this server URL + username.")
    async with websockets.serve(handler, args.host, args.port):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
