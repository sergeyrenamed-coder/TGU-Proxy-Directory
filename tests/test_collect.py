import importlib.util
import pathlib
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tgu_collect", ROOT / "scripts" / "collect.py")
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


class FakeSocket:
    def __init__(self, incoming):
        self.incoming = bytearray(incoming)
        self.sent = []
        self.closed = False

    def sendall(self, payload):
        self.sent.append(payload)

    def recv(self, size):
        chunk = self.incoming[:size]
        del self.incoming[:size]
        return bytes(chunk)

    def close(self):
        self.closed = True


class ProtocolByteTests(unittest.TestCase):
    def test_socks5_probe_sends_binary_greeting_and_connect_request(self):
        # SOCKS5: no-auth accepted, then CONNECT to 149.154.167.51:443 accepted.
        incoming = (
            bytes.fromhex("0500") +
            bytes.fromhex("05000001") +
            bytes([203, 0, 113, 7]) +
            bytes.fromhex("01bb")
        )
        fake = FakeSocket(incoming)
        with patch.object(collector, "connect_public_socket", return_value=fake):
            ok, _latency, reason = collector.probe_socks5_candidate("192.0.2.1", 1080)

        self.assertTrue(ok, reason)
        self.assertEqual(fake.sent[0], bytes.fromhex("050100"))
        self.assertEqual(
            fake.sent[1],
            bytes.fromhex("05010001") + bytes([149, 154, 167, 51]) + bytes.fromhex("01bb"),
        )
        self.assertTrue(fake.closed)

    def test_mtproto_transport_tags_are_real_binary_bytes(self):
        self.assertEqual(bytes.fromhex("eeeeeeee"), b"\xee\xee\xee\xee")
        self.assertEqual(bytes.fromhex("dddddddd"), b"\xdd\xdd\xdd\xdd")


if __name__ == "__main__":
    unittest.main()
