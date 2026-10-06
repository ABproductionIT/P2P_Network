import asyncio
import json
import os
import re
from http import HTTPStatus
from pathlib import Path

import websockets
from websockets.datastructures import Headers
from websockets.http11 import Response

PORT = int(os.getenv("PORT", "9000"))

# The client is served from the site root so its service worker controls the
# whole origin; root-relative navigations ("/search") then stay in the proxy.
ROOT = Path(__file__).resolve().parent
STATIC = {
    "/": ("client.html", "text/html; charset=utf-8"),
    "/client.html": ("client.html", "text/html; charset=utf-8"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
}

rooms = {}


def serve_static(connection, request):
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None
    entry = STATIC.get(request.path.split("?", 1)[0])
    if entry is None:
        return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")
    name, content_type = entry
    body = (ROOT / name).read_bytes()
    if name == "client.html":
        host = request.headers.get("Host", "")
        if not re.fullmatch(r"[A-Za-z0-9.\-\[\]:]+", host):
            host = f"127.0.0.1:{PORT}"
        body = body.replace(
            b'<meta name="signaling" content="">',
            f'<meta name="signaling" content="ws://{host}">'.encode(),
        )
    headers = Headers([
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-cache"),
    ])
    return Response(200, "OK", headers, body)


async def handler(ws):
    room_id = None
    try:
        async for raw in ws:
            message = json.loads(raw)

            if message.get("type") == "join":
                room_id = message["room"]
                room = rooms.setdefault(room_id, set())
                room.add(ws)
                print(f"[+] peer joined room={room_id}")
                await ws.send(json.dumps({"type": "joined", "room": room_id}))
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
        print(f"[-] peer disconnected room={room_id}")

async def main():
    print(f"Signaling server: ws://0.0.0.0:{PORT}")
    print(f"Browser client:   http://127.0.0.1:{PORT}/")
    async with websockets.serve(handler, "0.0.0.0", PORT, process_request=serve_static):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
