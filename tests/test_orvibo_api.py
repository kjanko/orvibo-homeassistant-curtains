"""Offline tests for the Orvibo protocol client."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.orvibo_curtain.api import OrviboClient


class TestPacketCodec(unittest.TestCase):
    """Test AES packet encode/decode round-trips."""

    def _make_client(self):
        return OrviboClient(
            host="example.org",
            port=10002,
            email="user@example.org",
            password_md5="A" * 32,
            family_id="family",
            device_id="device",
        )

    def test_round_trip_static_key(self):
        client = self._make_client()
        payload = {"cmd": 0, "serial": 123, "key": "0123456789ABCDEF", "status": 0}
        packet = client._build_packet(payload, use_dynamic=False)
        decoded = client._parse_packet(packet)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["cmd"], 0)
        self.assertEqual(decoded["status"], 0)

    def test_round_trip_dynamic_key(self):
        client = self._make_client()
        client._dynamic_key = b"0123456789ABCDEF"
        payload = {"cmd": 42, "value1": 77, "motoState": 1}
        packet = client._build_packet(payload, use_dynamic=True)
        decoded = client._parse_packet(packet)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["cmd"], 42)
        self.assertEqual(decoded["value1"], 77)

    def test_packet_header(self):
        client = self._make_client()
        packet = client._build_packet({"cmd": 0}, use_dynamic=False)
        self.assertEqual(packet[:2], b"hd")
        self.assertEqual(packet[4:6], b"pk")


if __name__ == "__main__":
    unittest.main()
