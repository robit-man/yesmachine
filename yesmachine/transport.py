"""TLS 1.2+ tunnels, certificate pinning and an application access key.

The native engine is a loopback upstream. A public connection authenticates
before any bytes are forwarded. Never send the key before verifying the pin.
"""

import asyncio
import hashlib
import hmac
import ssl
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .storage import private_write


def certificate(directory: Path):
    cert_path, key_path = directory / "host.crt", directory / "host.key"
    if not cert_path.exists() or not key_path.exists():
        key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.now(timezone.utc)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "YesMachine")])
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=3650))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        private_write(key_path, key.private_bytes(serialization.Encoding.PEM,
                      serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())
        private_write(cert_path, cert.public_bytes(serialization.Encoding.PEM).decode())
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text())
    return cert_path, key_path, hashlib.sha256(der).hexdigest()


async def pump(reader, writer):
    try:
        while chunk := await reader.read(65536):
            writer.write(chunk)
            await writer.drain()
    except (OSError, asyncio.CancelledError):
        pass


async def close_streams(*writers):
    for writer in writers:
        writer.close()
    await asyncio.gather(*(asyncio.wait_for(writer.wait_closed(), 1) for writer in writers),
                         return_exceptions=True)
    # A peer that never sends TLS close_notify must not keep Stop/Quit waiting.
    for writer in writers:
        writer.transport.abort()


async def bridge(left_reader, left_writer, right_reader, right_writer):
    tasks = [asyncio.create_task(pump(left_reader, right_writer)),
             asyncio.create_task(pump(right_reader, left_writer))]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await close_streams(left_writer, right_writer)


class Transport:
    def __init__(self, directory, access_key, event=lambda *_: None):
        self.cert, self.key, self.fingerprint = certificate(directory)
        self.access_key = access_key
        self.event = event
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.server = None
        self.viewers = []
        self.active = set()
        self.host_tasks = set()
        self.failures = defaultdict(deque)
        self.connections = 0

    def run(self, coroutine, timeout=15):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout)

    async def _serve_host(self, port, upstream_port, bind):
        if self.server:
            return
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.cert, self.key)

        async def accept(reader, writer):
            task = asyncio.current_task()
            self.active.add(task)
            self.host_tasks.add(task)
            peer = writer.get_extra_info("peername")[0]
            failed = self.failures[peer]
            now = time.monotonic()
            while failed and failed[0] < now - 60:
                failed.popleft()
            counted = False
            try:
                if len(failed) >= 5 or self.connections >= 32:
                    return
                line = await asyncio.wait_for(reader.readline(), 5)
                expected = ("YESMACHINE/1 " + self.access_key + "\n").encode()
                if not hmac.compare_digest(line, expected):
                    failed.append(now)
                    return
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", upstream_port), 5)
                writer.write(b"YESMACHINE/1 OK\n")
                await writer.drain()
                self.connections += 1
                counted = True
                self.event(f"Encrypted viewer transport opened from {peer}")
                await bridge(reader, writer, upstream_reader, upstream_writer)
            except (OSError, asyncio.TimeoutError, ValueError):
                pass
            finally:
                if counted:
                    self.connections -= 1
                await close_streams(writer)
                self.active.discard(task)
                self.host_tasks.discard(task)

        self.server = await asyncio.start_server(accept, bind, port, ssl=context,
                                                ssl_handshake_timeout=5, ssl_shutdown_timeout=1, limit=1024)
        return self.server.sockets[0].getsockname()[1]

    def start_host(self, port, upstream_port, bind="0.0.0.0"):
        return self.run(self._serve_host(port, upstream_port, bind))

    async def _remote(self, host, port, fingerprint, token):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # Explicit SHA-256 pin check follows, before credentials.
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=context, server_hostname=host, ssl_shutdown_timeout=1), 8)
        cert = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
        if not hmac.compare_digest(hashlib.sha256(cert).hexdigest(), fingerprint):
            await close_streams(writer)
            raise ValueError("The computer's certificate does not match. Verify its fingerprint on the host before reconnecting.")
        writer.write(("YESMACHINE/1 " + token + "\n").encode())
        await writer.drain()
        try:
            response = await asyncio.wait_for(reader.readline(), 5)
            if response != b"YESMACHINE/1 OK\n":
                raise ValueError("The host rejected the access key. Copy a fresh connection profile from that computer.")
        except BaseException:
            writer.close()
            raise
        return reader, writer

    async def _viewer(self, host, port, fingerprint, token):
        if len(self.viewers) >= 64:
            raise ValueError("The viewer tunnel limit was reached. Quit and reopen YesMachine before opening more sessions.")
        # Probe authenticity before opening the native viewer.
        reader, writer = await self._remote(host, port, fingerprint, token)
        await close_streams(writer)

        async def accept(local_reader, local_writer):
            task = asyncio.current_task()
            self.active.add(task)
            try:
                remote_reader, remote_writer = await self._remote(host, port, fingerprint, token)
                await bridge(local_reader, local_writer, remote_reader, remote_writer)
            except (OSError, ValueError, asyncio.TimeoutError) as error:
                self.event(f"Viewer tunnel closed: {error}", "error")
                local_writer.close()
            finally:
                self.active.discard(task)

        server = await asyncio.start_server(accept, "127.0.0.1", 0)
        self.viewers.append(server)
        return server.sockets[0].getsockname()[1]

    def viewer(self, device):
        if not device["fingerprint"] or not device["token"]:
            raise ValueError("Pair this computer first: import its YesMachine connection profile or enter its fingerprint and access key.")
        return self.run(self._viewer(device["host"], device["port"], device["fingerprint"], device["token"]), 20)

    async def _stop_host(self):
        server, self.server = self.server, None
        if server:
            server.close()
        tasks = list(self.host_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if server:
            await server.wait_closed()

    def stop_host(self):
        self.run(self._stop_host())

    def close(self):
        async def cleanup():
            await self._stop_host()
            for server in self.viewers:
                server.close()
            for task in list(self.active):
                task.cancel()
            await asyncio.gather(*list(self.active), return_exceptions=True)
            await asyncio.gather(*(server.wait_closed() for server in self.viewers))
        self.run(cleanup())
        async def cancel_remaining():
            # Drain callbacks and tasks before closing this dedicated loop.
            tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self.run(cancel_remaining())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)
        if not self.thread.is_alive():
            self.loop.close()
