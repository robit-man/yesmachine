import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from yesmachine.mobile import InstructionBuffer, MobileShare, instruction, read_instruction, unexpired
from yesmachine.compatibility import tcp_open


class GraphicsStreamTests(unittest.TestCase):
    def test_image_survives_tcp_splits_and_tunnel_keepalives(self):
        # Exercise every possible split, including length headers and image data.
        image = instruction('blob', 7, 'a.b,c;d' * 40)
        end = instruction('end', 7)
        ping = instruction('', 'ping', 1234)
        for split in range(len(image) + 1):
            buffer = InstructionBuffer()
            output = buffer.feed(image[:split])
            output.append(ping)
            output.extend(buffer.feed(image[split:] + end))
            parsed = InstructionBuffer().feed(''.join(output))
            self.assertEqual(sorted(parsed), sorted([image, end, ping]))

    def test_unicode_and_multiple_instructions_across_single_char_chunks(self):
        messages = [instruction('name', '桌面 🖥'), instruction('sync', 1234)]
        buffer = InstructionBuffer()
        actual = []
        for character in ''.join(messages):
            actual.extend(buffer.feed(character))
        self.assertEqual(actual, messages)

    def test_invalid_or_unbounded_instructions_are_rejected(self):
        for value in ('x.foo;', '999999999.x', '3.foo!'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                InstructionBuffer().feed(value)

    def test_status_probe_never_connects_to_rdp(self):
        with patch('socket.create_connection', side_effect=AssertionError('active probe')):
            self.assertIsInstance(tcp_open(3389), bool)


class ShareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.mobile = MobileShare(Path(self.temp.name), SimpleNamespace())

    def test_no_expiry_and_expired_share_authorization(self):
        identity = self.mobile.new_grant(0)
        self.mobile.sessions['cookie'] = {'grant': identity, 'expires': 0}
        request = SimpleNamespace(cookies={'yesmachine_phone': 'cookie'})
        self.assertTrue(self.mobile.authorized(request))
        self.assertTrue(unexpired(0))
        self.mobile.sessions['cookie']['expires'] = time.time() - 1
        self.assertFalse(self.mobile.authorized(request))

    def test_maintenance_cannot_stop_a_tunnel_while_starting(self):
        import threading
        self.mobile.tunnel=SimpleNamespace()
        with patch.object(self.mobile,'stop_tunnel') as stop:
            with self.mobile.lock:
                worker=threading.Thread(target=self.mobile.prune)
                worker.start();worker.join(timeout=1)
                self.assertFalse(worker.is_alive())
                stop.assert_not_called()
            self.mobile.prune()
            stop.assert_called_once()

    def test_cycle_revokes_cookies_and_changes_only_selected_key(self):
        first = self.mobile.new_grant(0)
        second = self.mobile.new_grant(900)
        first_key = self.mobile.grants[first]['key']
        second_key = self.mobile.grants[second]['key']
        self.mobile.sessions['cookie'] = {'id': 'viewer', 'grant': first, 'expires': 0}
        self.mobile.revoke(first, cycle=True)
        self.assertNotEqual(first_key, self.mobile.grants[first]['key'])
        self.assertEqual(second_key, self.mobile.grants[second]['key'])
        self.assertNotIn('cookie', self.mobile.sessions)
        self.assertEqual(self.mobile.grants[first]['expires'], 0)
        self.mobile.revoke(first)
        self.assertNotIn(first, self.mobile.grants)

class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncio
        import aiohttp
        self.temp = tempfile.TemporaryDirectory()
        self.mobile = MobileShare(Path(self.temp.name), SimpleNamespace())
        self.mobile.loop = asyncio.get_running_loop()
        await self.mobile._start_server()
        self.url = f'http://127.0.0.1:{self.mobile.local_port}'
        self.mobile.expected_origin = self.url
        self.identity = self.mobile.new_grant(0)
        self.client = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))

    async def asyncTearDown(self):
        await self.client.close()
        if self.mobile.desktop_task:
            self.mobile.desktop_task.cancel()
            import asyncio
            await asyncio.gather(self.mobile.desktop_task,return_exceptions=True)
        await self.mobile.runner.cleanup()
        self.temp.cleanup()

    async def login(self, key, origin=None):
        return await self.client.post(self.url+'/api/login',json={'key':key},headers={'Origin':origin or self.url})

    async def test_every_viewer_joins_warm_desktop_for_complete_snapshot(self):
        import asyncio
        selections = []
        syncs = []
        stopped = asyncio.Event()
        tasks = set()

        async def backend(reader, writer):
            task=asyncio.current_task();tasks.add(task);selected=['select','']
            try:
                selected=await read_instruction(reader)
                selections.append(selected[1])
                writer.write(instruction('args','VERSION_1_5_0').encode())
                await writer.drain()
                while (await read_instruction(reader))[0]!='connect':
                    pass
                writer.write(instruction('ready','$cached-desktop').encode())
                # The fake owner has a completed desktop frame. A joined user
                # receives the backend's full initial snapshot, without input.
                writer.write((instruction('size',0,640,480)+instruction('img',1,12,0,'image/png',0,0)+instruction('blob',1,'YWJj')+instruction('end',1)+instruction('sync',42)).encode())
                await writer.drain()
                while True:
                    message=await read_instruction(reader)
                    if message[0]=='sync':syncs.append(selected[1])
            except (asyncio.IncompleteReadError,ConnectionError):
                pass
            finally:
                if selected[1]=='rdp':stopped.set()
                writer.close();tasks.discard(task)

        server=await asyncio.start_server(backend,'127.0.0.1',0)
        self.mobile.guacd_port=server.sockets[0].getsockname()[1]
        self.mobile.compatibility=SimpleNamespace(record={'port':3389},rdp_credentials=lambda:{'username':'test','password':'private-test-password'})
        try:
            await self.login(self.mobile.grants[self.identity]['key'])
            for _ in range(2):
                ws=await self.client.ws_connect(self.url+'/tunnel',headers={'Origin':self.url},protocols=['guacamole'])
                received=''
                while '4.sync,2.42;' not in received:
                    message=await asyncio.wait_for(ws.receive(),3)
                    received+=message.data
                self.assertIn('4.size,1.0,3.640,3.480;',received)
                self.assertIn('3.img,1.1,2.12,1.0,9.image/png,1.0,1.0;',received)
                await ws.close()
                await asyncio.sleep(.02)
                self.assertFalse(stopped.is_set(),'Closing a browser must preserve the cached desktop')
                self.assertEqual(self.mobile.connection_id,'$cached-desktop')
            self.assertEqual(selections,['rdp','$cached-desktop','$cached-desktop'])
            self.assertIn('rdp',syncs,'The background owner must acknowledge frames')
        finally:
            self.mobile.desktop_task.cancel()
            await asyncio.gather(self.mobile.desktop_task,return_exceptions=True)
            self.mobile.desktop_task=None
            server.close();await server.wait_closed()
            for task in list(tasks):task.cancel()
            await asyncio.gather(*list(tasks),return_exceptions=True)

    async def test_phone_origin_auth_cookie_cycle_and_revocation(self):
        import asyncio
        key = self.mobile.grants[self.identity]['key']
        response = await self.login(key, 'https://untrusted.example')
        self.assertEqual(response.status, 403)
        response = await self.login(key)
        self.assertEqual(response.status, 200)
        cookie = response.cookies['yesmachine_phone']
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'],'Strict')
        response = await self.client.get(self.url+'/api/session')
        self.assertEqual((await response.json())['expires'], 0)
        status = str(self.mobile.status())
        self.assertNotIn(key,status)
        self.assertNotIn(cookie.value,status)
        for route in ('/api/state','/api/profile'):
            response = await self.client.get(self.url+route)
            self.assertEqual(response.status,404)
        await asyncio.to_thread(self.mobile.revoke,self.identity,True)
        response = await self.client.get(self.url+'/api/session')
        self.assertEqual(response.status,401)
        response = await self.login(key)
        self.assertEqual(response.status,403)
        new_key = self.mobile.grants[self.identity]['key']
        response = await self.login(new_key)
        self.assertEqual(response.status,200)
        await asyncio.to_thread(self.mobile.revoke,self.identity)
        response = await self.client.get(self.url+'/api/session')
        self.assertEqual(response.status,401)


if __name__ == '__main__':
    unittest.main()
