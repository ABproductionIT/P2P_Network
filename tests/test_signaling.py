import asyncio
import unittest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from signaling_server import create_app, STATE


class SignalingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = TestServer(create_app(max_peers=2))
        await self.server.start_server()
        self.session = ClientSession()
        self.clients = []

    async def asyncTearDown(self):
        for ws in self.clients:
            await ws.close()
        await self.session.close()
        await self.server.close()

    async def join(self, room='testroom', name='Alice'):
        ws = await self.session.ws_connect(self.server.make_url('/ws'))
        self.clients.append(ws)
        await ws.send_json({'type':'join', 'room':room, 'name':name})
        return ws, await self.read(ws)

    async def read(self, ws):
        return await asyncio.wait_for(ws.receive_json(), 2)

    async def test_routing_isolation_and_leave(self):
        a, aa = await self.join()
        b, bb = await self.join(name='Bob')
        self.assertEqual(bb['peers'], [{'id':aa['id'], 'name':'Alice'}])
        self.assertEqual((await self.read(a))['type'], 'peer-joined')
        c, cc = await self.join(room='anotherroom')
        data = {'description':{'type':'offer','sdp':'test'}}
        await a.send_json({'type':'signal','to':bb['id'],'from':'forged','data':data})
        self.assertEqual(await self.read(b), {'type':'signal','from':aa['id'],'data':data})
        await c.send_json({'type':'signal','to':aa['id'],'data':data})
        self.assertEqual((await self.read(c))['type'], 'error')
        await b.close()
        self.assertEqual(await self.read(a), {'type':'peer-left','id':bb['id']})

    async def test_validation_and_capacity(self):
        a, _ = await self.join()
        await a.send_str('not JSON')
        self.assertEqual((await self.read(a))['type'], 'error')
        await a.send_json({'type':'join','room':'otherroom','name':'A'})
        self.assertEqual((await self.read(a))['message'], 'Already joined')
        await a.send_json({'type':'chat','text':'must not be relayed'})
        self.assertEqual((await self.read(a))['type'], 'error')
        await self.join(name='B')
        c, msg = await self.join(name='C')
        self.assertEqual(msg['message'], 'Room full')
        self.assertEqual(len(self.server.app[STATE]['rooms']['testroom']), 2)

    async def test_http(self):
        async with self.session.get(self.server.make_url('/')) as response:
            self.assertEqual(response.status, 200)
            self.assertIn('chat.js', await response.text())
        async with self.session.get(self.server.make_url('/health')) as response:
            self.assertEqual(await response.json(), {'ok': True})

if __name__ == '__main__':
    unittest.main()
