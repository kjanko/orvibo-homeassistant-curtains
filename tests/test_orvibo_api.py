"""Offline tests for the Orvibo protocol client."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unittest import mock

from custom_components.orvibo_curtain.api import (
    OrviboApiError,
    OrviboAuthError,
    OrviboClient,
    OrviboCurtainApi,
    _parse_devices,
    _parse_families,
    _sign,
)


class TestSign(unittest.TestCase):
    """Request signatures skip empty values, like the Orvibo app."""

    def test_empty_values_do_not_change_signature(self):
        base = {"a": "1", "b": 2}
        self.assertEqual(_sign(base), _sign({**base, "c": "", "d": None}))

    def test_signature_is_order_independent(self):
        self.assertEqual(_sign({"a": "1", "b": "2"}), _sign({"b": "2", "a": "1"}))


class TestCloudParsing(unittest.TestCase):
    """Family and device list parsing."""

    def test_families_as_list(self):
        payload = {"data": [{"familyId": "f1", "familyName": "Home"}, {"familyName": "x"}]}
        self.assertEqual([f["familyId"] for f in _parse_families(payload)], ["f1"])

    def test_families_as_family_info_object(self):
        payload = {"data": {"familyInfo": [{"familyId": "f1"}, {"familyId": "f2"}]}}
        self.assertEqual([f["familyId"] for f in _parse_families(payload)], ["f1", "f2"])

    def test_devices_curtains_first_and_deleted_dropped(self):
        payload = {
            "data": {
                "device": [
                    {"deviceId": "1", "uid": "u1", "deviceName": "A plug", "deviceType": 1},
                    {"deviceId": "2", "uid": "u2", "deviceName": "Z curtain", "deviceType": 34},
                    {"deviceId": "3", "uid": "u3", "deviceName": "Old", "deviceType": 34, "delFlag": 1},
                    {"deviceId": "4", "deviceName": "No uid"},
                ]
            }
        }
        self.assertEqual([d["deviceId"] for d in _parse_devices(payload)], ["2", "1"])


def _client(device_id="dev", device_uid=None):
    return OrviboClient(
        host="example.org",
        port=10002,
        email="user@example.org",
        password_md5="A" * 32,
        family_id="family",
        device_id=device_id,
        device_uid=device_uid,
    )


class TestOwnReport(unittest.TestCase):
    """Matching position reports to this curtain."""

    def test_legacy_entry_uses_one_id_for_both(self):
        client = _client(device_id="uid1")
        self.assertTrue(client._is_own_report({"uid": "uid1"}))
        self.assertTrue(client._is_own_report({"deviceId": "uid1"}))

    def test_other_device_not_matched(self):
        client = _client(device_id="dev1", device_uid="uid1")
        self.assertFalse(client._is_own_report({"uid": "uid2", "deviceId": "dev2"}))
        self.assertTrue(client._is_own_report({"uid": "uid1", "deviceId": "dev1"}))


class TestQueryPosition(unittest.TestCase):
    """query_position prefers this curtain's report but never drops all reports."""

    def _query(self, client, replies):
        client._sock = mock.Mock()
        with mock.patch.object(OrviboClient, "_recv", side_effect=replies + [None] * 10), \
                mock.patch("custom_components.orvibo_curtain.api.time.time",
                           side_effect=[0, 0] + [1] * len(replies) + [10] * 20):
            return client.query_position()

    def test_matching_report_preferred(self):
        client = _client(device_id="dev1", device_uid="uid1")
        replies = [
            {"cmd": 42, "value1": 10, "uid": "other"},
            {"cmd": 42, "value1": 89, "uid": "uid1"},
        ]
        self.assertEqual(self._query(client, replies), 89)

    def test_unmatched_ids_fall_back_to_first_report(self):
        # Regression: WiFi motors report IDs the entry doesn't know.
        client = _client(device_id="fa4b")
        replies = [{"cmd": 42, "value1": 89, "uid": "x", "deviceId": "y"}]
        self.assertEqual(self._query(client, replies), 89)

    def test_no_reports_returns_none(self):
        self.assertIsNone(self._query(_client(), []))


class TestSessionErrors(unittest.TestCase):
    """Login rejections are auth errors; timeouts are not."""

    def _api_with_login(self, login_reply):
        api = OrviboCurtainApi("example.org", "user@example.org", "A" * 32, "family", "dev")
        patches = [
            mock.patch.object(OrviboClient, "connect"),
            mock.patch.object(OrviboClient, "handshake", return_value=True),
            mock.patch.object(OrviboClient, "login", return_value=login_reply),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return api

    def test_rejected_login_is_auth_error(self):
        api = self._api_with_login({"cmd": 2, "status": 12})
        with self.assertRaises(OrviboAuthError):
            api.validate_credentials()

    def test_login_timeout_is_not_auth_error(self):
        api = self._api_with_login(None)
        with self.assertRaises(OrviboApiError) as ctx:
            api.validate_credentials()
        self.assertNotIsInstance(ctx.exception, OrviboAuthError)

    def test_unacknowledged_command_raises(self):
        api = self._api_with_login({"cmd": 2, "status": 0})
        with mock.patch.object(OrviboClient, "send_control", return_value=False):
            with self.assertRaises(OrviboApiError):
                api.set_position(90)


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
