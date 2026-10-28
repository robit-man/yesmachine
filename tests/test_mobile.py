import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from yesmachine.mobile import InstructionBuffer, MobileShare, instruction, unexpired
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


if __name__ == '__main__':
    unittest.main()

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
        await self.mobile.runner.cleanup()
        self.temp.cleanup()

    async def login(self, key, origin=None):
        return await self.client.post(self.url+'/api/login',json={'key':key},headers={'Origin':origin or self.url})

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
