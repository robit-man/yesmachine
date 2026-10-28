import asyncio
import http.client
import json
import socket
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from yesmachine.app import Application, Handler
from yesmachine.engine import update_options
from yesmachine.storage import Store, validate_device
from yesmachine.transport import Transport


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.addCleanup(self.store.db.close)

    def test_crud_keeps_key_out_of_status_and_preserves_it_on_edit(self):
        value = dict(name="Basilisk", host="192.168.1.52", port=21180,
                     fingerprint="a"*64, token="b"*43, mac="")
        identity = self.store.save_device(value)
        self.assertNotIn("token", self.store.devices()[0])
        self.assertTrue(self.store.devices()[0]["paired"])
        self.store.save_device(dict(value, id=identity, name="Renamed", token=""))
        self.assertEqual(self.store.device(identity)["token"], "b"*43)
        self.store.remove_device(identity)
        self.assertEqual(self.store.devices(), [])

    def test_rejects_shell_and_url_targets(self):
        for host in ["-options", "$(touch /tmp/no)", "foo;bar", "https://192.168.1.52", "a b", "a\n--option"]:
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate_device(dict(name="Test", host=host))

    def test_rejects_bad_ports_and_pairing(self):
        for changes in [dict(port=0), dict(port=65536), dict(port="abc"),
                        dict(fingerprint="abc"), dict(token="abc"), dict(mac="invalid")]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_device(dict(name="Test", host="localhost", **changes))

    def test_bounded_activity(self):
        for number in range(510):
            self.store.event(str(number))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM activity").fetchone()[0], 500)
        self.assertEqual(len(self.store.activity()), 100)

    def test_config_update_preserves_other_sections(self):
        import tomllib
        path = Path(self.temp.name) / "engine.toml"
        path.write_text('id = "abc"\n[options]\ndirect-server = "N"\nother = "kept"\n[next]\nvalue = 5\n')
        update_options(path, {"direct-server":"Y", "whitelist":"127.0.0.1"})
        parsed = tomllib.loads(path.read_text())
        self.assertEqual(parsed["options"]["other"], "kept")
        self.assertEqual(parsed["options"]["direct-server"], "Y")
        self.assertEqual(parsed["next"]["value"], 5)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.transport = Transport(Path(self.temp.name), "x"*43)
        self.addCleanup(self.transport.close)
        self.upstream_connections = 0

        async def make_echo():
            async def echo(reader, writer):
                self.upstream_connections += 1
                try:
                    while chunk := await reader.read(65536):
                        writer.write(chunk)
                        await writer.drain()
                finally:
                    writer.close()
            return await asyncio.start_server(echo, "127.0.0.1", 0)
        self.echo = self.transport.run(make_echo())
        self.addCleanup(lambda: self.transport.loop.call_soon_threadsafe(self.echo.close))
        self.upstream_port = self.echo.sockets[0].getsockname()[1]
        self.port = self.transport.start_host(0, self.upstream_port, "127.0.0.1")
        self.device = dict(host="127.0.0.1", port=self.port, fingerprint=self.transport.fingerprint, token="x"*43)

    def test_tunnel_round_trip_for_large_binary_payload(self):
        local = self.transport.viewer(self.device)
        payload = bytes(range(256)) * 1024
        with socket.create_connection(("127.0.0.1", local), timeout=5) as client:
            client.sendall(payload)
            received = b""
            while len(received) < len(payload):
                received += client.recv(65536)
            self.assertEqual(received, payload)

    def test_wrong_fingerprint_never_reaches_upstream(self):
        with self.assertRaisesRegex(ValueError, "certificate"):
            self.transport.viewer(dict(self.device, fingerprint="0"*64))
        self.assertEqual(self.upstream_connections, 0)

    def test_wrong_access_key_never_reaches_upstream(self):
        with self.assertRaisesRegex(ValueError, "access key"):
            self.transport.viewer(dict(self.device, token="y"*43))
        self.assertEqual(self.upstream_connections, 0)

    def test_pairing_is_mandatory(self):
        with self.assertRaisesRegex(ValueError, "Pair"):
            self.transport.viewer(dict(self.device, token=""))

    def test_stop_host_closes_existing_viewer_transport(self):
        local = self.transport.viewer(self.device)
        with socket.create_connection(("127.0.0.1", local), timeout=5) as client:
            client.sendall(b"before stop")
            self.assertEqual(client.recv(100), b"before stop")
            self.transport.stop_host()
            self.assertEqual(client.recv(100), b"")


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.app = Application(state=Path(cls.temp.name))
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.app, cls.server.secret = cls.app, "test-session-key"
        cls.app.http = cls.server
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.app.close()
        cls.app.store.db.close()
        cls.temp.cleanup()

    def request(self, path, payload=None, headers=None, authenticated=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        request_headers = {"Content-Type":"application/json"}
        if authenticated:
            request_headers.update({"Cookie":"yesmachine=test-session-key", "X-YesMachine":"1"})
        request_headers.update(headers or {})
        connection.request("GET" if payload is None else "POST", path,
                           body=None if payload is None else json.dumps(payload), headers=request_headers)
        response = connection.getresponse()
        status, result = response.status, response.read()
        connection.close()
        return status, result

    def test_dns_rebinding_host_rejected(self):
        self.assertEqual(self.request("/", headers={"Host":"attacker.example"})[0], 403)

    def test_cross_origin_write_rejected(self):
        self.assertEqual(self.request("/api/devices/save", {}, headers={"Origin":"https://attacker.example"})[0], 403)

    def test_api_requires_session_and_custom_header(self):
        self.assertEqual(self.request("/api/state", authenticated=False)[0], 403)
        self.assertEqual(self.request("/api/state", headers={"X-YesMachine":""})[0], 403)

    def test_http_crud_and_secret_redaction(self):
        code, value = self.request("/api/devices/save", dict(name="Test",host="localhost"))
        self.assertEqual(code,200)
        identity = json.loads(value)["id"]
        code, value = self.request("/api/state")
        self.assertEqual(code,200)
        self.assertNotIn(self.app.store.settings["access_key"].encode(), value)
        self.assertEqual(self.request("/api/devices/remove", {"id":identity})[0],200)

    def test_import_rejects_foreign_format(self):
        self.assertEqual(self.request("/api/devices/import", {"profile":{"format":"other"}})[0],400)

    def test_key_rotation_revokes_old_profile(self):
        old = self.app.profile()["token"]
        self.app.perform("key-rotate", {})
        self.assertNotEqual(self.app.profile()["token"], old)
        self.assertEqual(self.app.transport.access_key, self.app.profile()["token"])

    def test_static_path_traversal_rejected(self):
        code, value = self.request("/../../.state/settings.json")
        self.assertEqual(code,404)
        self.assertNotIn(b"access_key",value)


if __name__ == "__main__":
    unittest.main()
