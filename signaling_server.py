#!/usr/bin/env python3
"""Single-process HTTP + WebSocket signaling; chat payloads never use this server."""
import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import time
from aiohttp import web, WSMsgType

ROOT = Path(__file__).parent
ROOM = re.compile(r'^[A-Za-z0-9_-]{8,80}$')
STATE = web.AppKey('state', dict)


def ice_servers(peer_id):
    servers = [{'urls': ['stun:stun.l.google.com:19302']}]
    urls = [x.strip() for x in os.getenv('TURN_URLS', '').split(',') if x.strip()]
    secret = os.getenv('TURN_SECRET')
    if urls and secret:
        username = f'{int(time.time()) + 3600}:{peer_id}'
        credential = base64.b64encode(hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
        servers.append({'urls': urls, 'username': username, 'credential': credential})
    return servers


async def send(ws, message):
    try:
        await asyncio.wait_for(ws.send_json(message), 5)
    except (ConnectionError, RuntimeError, asyncio.TimeoutError):
        return False
    return True


async def socket(request):
    state = request.app[STATE]
    allowed = state['origins']
    if allowed and request.headers.get('Origin') not in allowed:
        raise web.HTTPForbidden(text='Origin not allowed')
    if len(state['sockets']) >= 1000:
        raise web.HTTPServiceUnavailable(text='Server full')
    ws = web.WebSocketResponse(heartbeat=25, max_msg_size=65536, compress=False)
    state['sockets'].add(ws)
    peer_id, room_id = secrets.token_hex(12), None
    tokens, last = 180.0, time.monotonic()
    try:
        await ws.prepare(request)
        async for raw in ws:
            if raw.type != WSMsgType.TEXT:
                break
            now = time.monotonic()
            tokens = min(180, tokens + (now - last) * 30) - 1
            last = now
            if tokens < 0:
                await ws.close(code=1008, message=b'Rate limit')
                break
            try:
                msg = json.loads(raw.data)
                if not isinstance(msg, dict):
                    raise ValueError('Expected JSON object')
                kind = msg.get('type')
                if kind == 'join':
                    if room_id:
                        raise ValueError('Already joined')
                    rid, name = msg.get('room'), msg.get('name')
                    if not isinstance(rid, str) or not ROOM.fullmatch(rid):
                        raise ValueError('Room: 8-80 letters, digits, underscores or hyphens')
                    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
                        raise ValueError('Name: 1-40 characters')
                    room = state['rooms'].setdefault(rid, {})
                    if len(room) >= state['max_peers']:
                        raise ValueError('Room full')
                    # No await between snapshot and insertion: concurrent joins see each other.
                    peers = [{'id': k, 'name': v['name']} for k, v in room.items()]
                    room_id = rid
                    room[peer_id] = {'ws': ws, 'name': name.strip()}
                    await send(ws, {'type': 'joined', 'id': peer_id, 'peers': peers,
                                    'iceServers': ice_servers(peer_id)})
                    for p in peers:
                        if p['id'] in room:
                            await send(room[p['id']]['ws'], {'type': 'peer-joined', 'id': peer_id, 'name': name.strip()})
                elif kind == 'signal':
                    room = state['rooms'].get(room_id, {})
                    target = msg.get('to')
                    if not isinstance(target, str) or target == peer_id or target not in room:
                        raise ValueError('Peer unavailable in this room')
                    data = msg.get('data')
                    if not isinstance(data, dict) or len(data) != 1:
                        raise ValueError('Invalid signal')
                    if 'description' in data:
                        desc = data['description']
                        if not isinstance(desc, dict) or desc.get('type') not in ('offer', 'answer') or not isinstance(desc.get('sdp'), str):
                            raise ValueError('Invalid SDP')
                        data = {'description': {'type': desc['type'], 'sdp': desc['sdp']}}
                    elif 'candidate' in data:
                        if data['candidate'] is not None and not isinstance(data['candidate'], dict):
                            raise ValueError('Invalid ICE candidate')
                    else:
                        raise ValueError('Only SDP and ICE are allowed')
                    await send(room[target]['ws'], {'type': 'signal', 'from': peer_id, 'data': data})
                else:
                    raise ValueError('Unsupported message type')
            except (ValueError, TypeError) as exc:
                await send(ws, {'type': 'error', 'message': str(exc)})
    finally:
        state['sockets'].discard(ws)
        room = state['rooms'].get(room_id)
        if room is not None:
            room.pop(peer_id, None)
            if not room:
                state['rooms'].pop(room_id, None)
            for peer in list(room.values()):
                await send(peer['ws'], {'type': 'peer-left', 'id': peer_id})
    return ws


async def shutdown(app):
    await asyncio.gather(*(ws.close(code=1001, message=b'Server shutdown') for ws in list(app[STATE]['sockets'])))


async def index(request):
    return web.FileResponse(ROOT / 'docs/index.html', headers={'Cache-Control': 'no-store'})


async def javascript(request):
    return web.FileResponse(ROOT / 'docs/chat.js', headers={'Cache-Control': 'no-store'})


async def health(request):
    return web.json_response({'ok': True})


def create_app(max_peers=8):
    app = web.Application()
    app[STATE] = {'rooms': {}, 'sockets': set(), 'max_peers': max_peers,
                  'origins': {s.strip() for s in os.getenv('ALLOWED_ORIGINS', '').split(',') if s.strip()}}
    app.add_routes([web.get('/', index), web.get('/index.html', index), web.get('/chat.js', javascript),
                    web.get('/ws', socket), web.get('/health', health)])
    app.on_shutdown.append(shutdown)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default=os.getenv('HOST', '0.0.0.0'))
    parser.add_argument('--port', type=int, default=int(os.getenv('PORT', '9000')))
    args = parser.parse_args()
    web.run_app(create_app(), host=args.host, port=args.port, access_log=None)
