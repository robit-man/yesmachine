import asyncio
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from yesmachine.vnc import VncServer, PIXEL_FORMAT, challenge_response, encode_pixels


class Canvas:
    def __init__(self):
        self.frame = Image.new('RGB', (16, 12), (240, 120, 30))
        self.events = []
    async def key(self, *args): self.events.append(('key', args))
    async def pointer(self, *args): self.events.append(('pointer', args))
    async def clipboard(self, *args): self.events.append(('clipboard', args))
    async def close(self): pass


class VncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.canvas = Canvas()
        self.server = VncServer(Path(self.temp.name), SimpleNamespace())
        # Pick a free port, then bind only to loopback for these wire tests.
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0));port = sock.getsockname()[1]
        await self.server.start('testonly', port, bind='127.0.0.1', canvas=self.canvas)

    async def asyncTearDown(self):
        await self.server.stop()
        self.temp.cleanup()

    async def connect(self, version=b'RFB 003.008\n', password='testonly'):
        r,w = await asyncio.open_connection('127.0.0.1', self.server.port)
        self.addAsyncCleanup(self.close_writer, w)
        self.assertEqual(await r.readexactly(12), b'RFB 003.008\n')
        w.write(version);await w.drain()
        if version == b'RFB 003.003\n':
            self.assertEqual(await r.readexactly(4), struct.pack('>I', 2))
        else:
            self.assertEqual(await r.readexactly(3), b'\x02\x13\x02')
            w.write(b'\x02');await w.drain()
        challenge = await r.readexactly(16)
        w.write(challenge_response(password, challenge));await w.drain()
        result = struct.unpack('>I', await r.readexactly(4))[0]
        return r,w,result

    async def close_writer(self, w):
        w.close();await w.wait_closed()

    async def initialize(self,r,w):
        w.write(b'\x01');await w.drain()
        width,height=struct.unpack('>HH',await r.readexactly(4))
        self.assertEqual((width,height),self.canvas.frame.size)
        self.assertEqual(await r.readexactly(16), PIXEL_FORMAT)
        n=struct.unpack('>I',await r.readexactly(4))[0];await r.readexactly(n)

    async def test_legacy_33_and_37_clients_authenticate(self):
        for version in (b'RFB 003.003\n',b'RFB 003.007\n'):
            r,w,result=await self.connect(version)
            self.assertEqual(result,0)
            await self.initialize(r,w)
            await self.close_writer(w)

    async def test_wrong_password_never_receives_desktop(self):
        r,w,result=await self.connect(password='wrongkey')
        self.assertEqual(result,1)
        length=struct.unpack('>I',await r.readexactly(4))[0]
        self.assertEqual(await r.readexactly(length),b'Incorrect VNC key.')
        self.assertEqual(await r.read(),b'')

    async def test_compressed_frame_and_input_round_trip(self):
        r,w,result=await self.connect();self.assertEqual(result,0)
        await self.initialize(r,w)
        w.write(b'\x02\x00\x00\x02'+struct.pack('>ii',6,0))
        w.write(b'\x03'+struct.pack('>BHHHH',0,0,0,16,12));await w.drain()
        self.assertEqual(await r.readexactly(4),b'\x00\x00\x00\x01')
        rect=struct.unpack('>HHHHi',await r.readexactly(12));self.assertEqual(rect,(0,0,16,12,6))
        n=struct.unpack('>I',await r.readexactly(4))[0]
        pixels=zlib.decompressobj().decompress(await r.readexactly(n))
        self.assertEqual(pixels,encode_pixels(self.canvas.frame,PIXEL_FORMAT))
        w.write(b'\x04'+struct.pack('>BHI',1,0,65))
        w.write(b'\x05'+struct.pack('>BHH',1,4,6))
        w.write(b'\x06\0\0\0'+struct.pack('>I',5)+b'hello');await w.drain()
        for _ in range(30):
            if len(self.canvas.events)==3:break
            await asyncio.sleep(.01)
        self.assertEqual(self.canvas.events,[('key',(1,65)),('pointer',(1,4,6)),('clipboard',('hello',))])

    def test_pixel_formats_and_invalid_masks(self):
        rgb565=struct.pack('>BBBBHHHBBB3x',16,16,0,1,31,63,31,11,5,0)
        self.assertEqual(len(encode_pixels(self.canvas.frame,rgb565)),16*12*2)
        bad=struct.pack('>BBBBHHHBBB3x',32,24,0,1,255,255,255,0,0,0)
        with self.assertRaises(ValueError):encode_pixels(self.canvas.frame,bad)
