"""Orvibo cloud protocol client for the Home Assistant integration.

Handles:
- REST API (OAuth login → host, families and device list)
- Binary socket protocol (TLS mutual-auth, AES-ECB encrypted JSON commands)
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import json
import logging
import os
import random
import select
import socket
import ssl
import struct
import time
from urllib import parse, request
import zlib

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

_LOGGER = logging.getLogger(__name__)

_STATIC_KEY = b"khggd54865SNJHGF"
_HMAC_SECRET = "nQ45RjPtOws96jmH"
_INTEGRATION_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_VERSION = "5.2.5.301"

# Device types known to be curtain motors (34 = curtain, 35 = roller shutter).
# WiFi motors may report other types, so these only sort the device list.
CURTAIN_DEVICE_TYPES = {34, 35}


class OrviboApiError(Exception):
    """Raised when communication with the Orvibo cloud fails."""


class OrviboAuthError(OrviboApiError):
    """Raised when the Orvibo cloud rejects the account credentials."""


# ---------------------------------------------------------------------------
# REST API — account login, family list and device list (setup only)
# ---------------------------------------------------------------------------


@dataclass
class CloudAccount:
    """A logged-in Orvibo REST session."""

    host: str
    access_token: str
    user_id: str
    families: list[dict] = field(default_factory=list)


def _sign(params: dict) -> str:
    """HMAC-SHA256 request signature: sorted non-empty params, then the key."""
    items = [
        f"{k}={params[k]}"
        for k in sorted(params)
        if params[k] is not None and str(params[k]).strip()
    ]
    items.append(f"key={_HMAC_SECRET}")
    return hmac.new(
        _HMAC_SECRET.encode(), "&".join(items).encode(), hashlib.sha256
    ).hexdigest().upper()


def _rest_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _post_signed(host: str, path: str, params: dict) -> dict:
    body = dict(params)
    body["sign"] = _sign(params)
    req = request.Request(
        f"https://{host}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=15, context=_rest_context()) as resp:
        return json.loads(resp.read().decode())


def _parse_families(payload: dict) -> list[dict]:
    """Family list; `data` is either a list or {"familyInfo": [...]}."""
    raw = payload.get("data")
    if isinstance(raw, dict):
        raw = raw.get("familyInfo", [])
    if not isinstance(raw, list):
        return []
    return [f for f in raw if isinstance(f, dict) and f.get("familyId")]


def cloud_login(email: str, password_md5: str, hosts: list[str]) -> CloudAccount:
    """Log in on the first regional host that accepts the account.

    Raises OrviboAuthError if a host rejected the credentials and none accepted
    them, OrviboApiError if no host could be reached.
    """
    rejected = False
    for host in hosts:
        try:
            params = parse.urlencode({"userName": email, "type": "0", "password": password_md5})
            req = request.Request(f"https://{host}/getOauthToken?{params}", method="GET")
            with request.urlopen(req, timeout=15, context=_rest_context()) as resp:
                oauth = json.loads(resp.read().decode())
        except Exception as exc:
            _LOGGER.debug("OAuth failed on %s: %s", host, exc)
            continue

        # Response is {"data": {"access_token": ..., "user_id": ...}, "status": 0}
        data = oauth.get("data") if isinstance(oauth.get("data"), dict) else oauth
        token = data.get("access_token") or data.get("accessToken")
        user_id = data.get("user_id") or data.get("userId")
        if not token or not user_id:
            rejected = True
            continue

        try:
            family_resp = _post_signed(
                host,
                "/v2/family/statistics/users",
                {
                    "accessToken": token,
                    "userId": user_id,
                    "timestamp": str(int(time.time() * 1000)),
                    "random": str(random.randint(100000, 999999)),
                },
            )
        except Exception as exc:
            _LOGGER.debug("Family query failed on %s: %s", host, exc)
            continue

        families = _parse_families(family_resp)
        if families:
            _LOGGER.debug("Logged in on %s with %d families", host, len(families))
            return CloudAccount(host, token, user_id, families)

    if rejected:
        raise OrviboAuthError("Orvibo cloud rejected the email or password.")
    raise OrviboApiError("Could not connect to Orvibo cloud.")


def _parse_devices(payload: dict) -> list[dict]:
    """Device records from a readtable response, curtain types first."""
    data = payload.get("data")
    raw = data.get("device", []) if isinstance(data, dict) else []
    devices = [
        d
        for d in raw
        if isinstance(d, dict)
        and d.get("deviceId")
        and d.get("uid")
        and str(d.get("delFlag", 0)) != "1"
    ]
    return sorted(devices, key=lambda d: (not is_curtain(d), str(d.get("deviceName", ""))))


def is_curtain(device: dict) -> bool:
    """True if a device record has a known curtain motor type."""
    try:
        return int(device.get("deviceType")) in CURTAIN_DEVICE_TYPES
    except (TypeError, ValueError):
        return False


def fetch_devices(account: CloudAccount, email: str, family_id: str) -> list[dict]:
    """List the devices in a family (deviceId, uid, deviceName, deviceType)."""
    try:
        payload = _post_signed(
            account.host,
            "/v2/cmd/app/readtable",
            {
                "accessToken": account.access_token,
                "dataType": "all",
                "deviceFlag": 0,
                "familyId": family_id,
                "lastUpdateTime": 0,
                "pageIndex": 0,
                "random": str(random.randint(100000, 999999)),
                "serial": random.randint(100000000, 999999999),
                "timestamp": int(time.time() * 1000),
                "userId": account.user_id,
                "userName": email,
                "ver": _APP_VERSION,
            },
        )
    except Exception as exc:
        raise OrviboApiError(f"Device list request failed: {exc}") from exc
    if payload.get("status", 0) != 0 or payload.get("code", 0) != 0:
        raise OrviboApiError("Orvibo cloud rejected the device list request.")
    return _parse_devices(payload)


# ---------------------------------------------------------------------------
# Binary protocol client (TLS mutual-auth socket on port 10002)
# ---------------------------------------------------------------------------


class OrviboClient:
    """Low-level Orvibo binary protocol over TLS."""

    def __init__(
        self,
        host: str,
        port: int,
        email: str,
        password_md5: str,
        family_id: str,
        device_id: str,
        device_uid: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._email = email
        self._password_md5 = password_md5
        self._family_id = family_id
        self._device_id = device_id
        # Entries made before device discovery only know one ID (the uid)
        # and send it as both uid and deviceId.
        self._device_uid = device_uid or device_id

        self._sock: ssl.SSLSocket | None = None
        self._session_id = "0" * 32
        self._dynamic_key: bytes | None = None
        self._serial = int(time.time())

    def _next_serial(self) -> int:
        self._serial += 1
        return self._serial

    @staticmethod
    def _encrypt(plaintext: bytes, key: bytes) -> bytes:
        return AES.new(key, AES.MODE_ECB).encrypt(pad(plaintext, 16))

    @staticmethod
    def _decrypt(ciphertext: bytes, key: bytes) -> bytes:
        return unpad(AES.new(key, AES.MODE_ECB).decrypt(ciphertext), 16)

    def _build_packet(self, payload: dict, use_dynamic: bool = True) -> bytes:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        key = (self._dynamic_key if use_dynamic and self._dynamic_key else _STATIC_KEY)
        flag = b"dk" if (use_dynamic and self._dynamic_key) else b"pk"

        encrypted = self._encrypt(raw, key)
        crc = zlib.crc32(encrypted) & 0xFFFFFFFF
        crc_bytes = bytes.fromhex(f"{crc:08X}")
        session = self._session_id.encode("ascii")[:32].ljust(32, b"0")
        length = 42 + len(encrypted)

        return b"hd" + struct.pack(">H", length) + flag + crc_bytes + session + encrypted

    def _parse_packet(self, data: bytes) -> dict | None:
        if len(data) < 42 or data[:2] != b"hd":
            return None
        pkt_len = struct.unpack(">H", data[2:4])[0]
        encrypted = data[42:pkt_len]

        session = data[10:42].decode("ascii", errors="replace").rstrip("\x00")
        if session and session != "0" * 32 and len(session) > 10:
            self._session_id = session.ljust(32, "0")[:32]

        if not encrypted or len(encrypted) % 16 != 0:
            return None

        for key in (self._dynamic_key, _STATIC_KEY):
            if key is None:
                continue
            try:
                return json.loads(self._decrypt(encrypted, key).decode())
            except Exception:
                continue
        return None

    def _recv(self, timeout: float = 10.0) -> dict | None:
        if not self._sock:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            ready = select.select([self._sock], [], [], min(remaining, 1.0))
            if not ready[0]:
                continue
            try:
                data = self._sock.recv(8192)
            except (ssl.SSLError, OSError):
                return None
            if not data:
                return None
            offset = 0
            while offset < len(data) - 42:
                if data[offset:offset + 2] != b"hd":
                    offset += 1
                    continue
                pkt_len = struct.unpack(">H", data[offset + 2:offset + 4])[0]
                if offset + pkt_len > len(data):
                    break
                result = self._parse_packet(data[offset:offset + pkt_len])
                if result is not None:
                    return result
                offset += pkt_len
        return None

    def connect(self) -> None:
        cert_path = os.path.join(_INTEGRATION_DIR, "orvibo_client_cert.pem")
        key_path = os.path.join(_INTEGRATION_DIR, "orvibo_client_key.pem")

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)

        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(15)
        self._sock = ctx.wrap_socket(raw, server_hostname=self._host)
        self._sock.connect((self._host, self._port))

    def disconnect(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def handshake(self) -> bool:
        payload = {
            "source": "ZhiJia365",
            "softwareVersion": "50205301",
            "sysVersion": "Android12_32",
            "hardwareVersion": "SHARP SH-R80P",
            "language": "en",
            "identifier": "fee6c365cd560888",
            "phoneName": "SHARP SH-R80P",
            "cmd": 0,
            "serial": self._next_serial(),
            "clientType": 1,
            "uniSerial": int(time.time() * 1000),
            "serverRecord": False,
            "ver": _APP_VERSION,
            "debugInfo": f"Android_ZhiJia365_32_{_APP_VERSION}",
        }
        self._sock.sendall(self._build_packet(payload, use_dynamic=False))
        resp = self._recv(timeout=10)
        if resp and resp.get("cmd") == 0 and "key" in resp:
            self._dynamic_key = resp["key"].encode()[:16]
            sid = resp.get("sessionId")
            if isinstance(sid, str):
                self._session_id = sid.ljust(32, "0")[:32]
            return True
        return False

    def login(self) -> dict | None:
        """Send the login command and return the server's reply (None on timeout)."""
        payload = {
            "userName": self._email,
            "password": self._password_md5,
            "familyId": self._family_id,
            "type": 4,
            "needAccountDetailError": True,
            "cmd": 2,
            "serial": self._next_serial(),
            "clientType": 1,
            "uniSerial": int(time.time() * 1000),
            "serverRecord": False,
            "ver": _APP_VERSION,
            "debugInfo": f"Android_ZhiJia365_32_{_APP_VERSION}",
        }
        self._sock.sendall(self._build_packet(payload))
        return self._recv(timeout=10)

    def _is_own_report(self, resp: dict) -> bool:
        """True if a status report names this curtain."""
        ids = {self._device_id, self._device_uid}
        return resp.get("uid") in ids or resp.get("deviceId") in ids

    def query_position(self) -> int | None:
        payload = {
            "familyId": self._family_id,
            "cmd": 230,
            "serial": self._next_serial(),
            "clientType": 1,
            "uniSerial": int(time.time() * 1000),
            "serverRecord": False,
            "ver": _APP_VERSION,
            "debugInfo": f"Android_ZhiJia365_32_{_APP_VERSION}",
        }
        self._sock.sendall(self._build_packet(payload))
        # Prefer a report that names this curtain. Reports don't always carry
        # the IDs the entry knows (WiFi motors report other IDs), so fall back
        # to the first report, as before.
        fallback: int | None = None
        deadline = time.time() + 5
        while time.time() < deadline:
            resp = self._recv(timeout=2)
            if not resp:
                continue
            if resp.get("cmd") == 42 and "value1" in resp:
                position = max(0, min(100, int(resp["value1"])))
                _LOGGER.debug(
                    "Position report %s (uid=%s deviceId=%s)",
                    position, resp.get("uid"), resp.get("deviceId"),
                )
                if self._is_own_report(resp):
                    return position
                if fallback is None:
                    fallback = position
        return fallback

    def send_control(self, order: str, value1: int) -> tuple[bool, list[dict]]:
        """Send a control command; return (confirmed, replies seen while waiting)."""
        payload = {
            "uid": self._device_uid,
            "userName": self._email,
            "deviceId": self._device_id,
            "groupId": "",
            "order": order,
            "value1": value1,
            "value2": 0,
            "value3": 0,
            "value4": 0,
            "delayTime": 0,
            "qualityOfService": 1,
            "defaultResponse": 1,
            "propertyResponse": 0,
            "cmd": 15,
            "serial": self._next_serial(),
            "clientType": 1,
            "uniSerial": int(time.time() * 1000),
            "serverRecord": False,
            "ver": _APP_VERSION,
            "debugInfo": f"Android_ZhiJia365_32_{_APP_VERSION}",
        }
        self._sock.sendall(self._build_packet(payload))
        # The cloud may send other packets (interim statuses, status reports)
        # before confirming, so read for a while instead of trusting the first.
        replies: list[dict] = []
        deadline = time.time() + 5
        while time.time() < deadline:
            resp = self._recv(timeout=min(2.0, max(0.1, deadline - time.time())))
            if not resp:
                continue
            replies.append(resp)
            if resp.get("status") == 0 or resp.get("cmd") == 42:
                return True, replies
        return False, replies


# ---------------------------------------------------------------------------
# High-level API used by the HA coordinator
# ---------------------------------------------------------------------------


class OrviboCurtainApi:
    """Manages short-lived sessions to the Orvibo binary protocol."""

    def __init__(
        self,
        host: str,
        email: str,
        password_md5: str,
        family_id: str,
        device_id: str,
        device_uid: str | None = None,
    ) -> None:
        self._host = host
        self._email = email
        self._password_md5 = password_md5
        self._family_id = family_id
        self._device_id = device_id
        self._device_uid = device_uid

    def _open_session(self) -> OrviboClient:
        from .const import DEFAULT_PORT
        client = OrviboClient(
            host=self._host,
            port=DEFAULT_PORT,
            email=self._email,
            password_md5=self._password_md5,
            family_id=self._family_id,
            device_id=self._device_id,
            device_uid=self._device_uid,
        )
        client.connect()
        if not client.handshake():
            client.disconnect()
            raise OrviboApiError("Handshake failed")
        resp = client.login()
        if not resp or resp.get("status") != 0:
            client.disconnect()
            if resp:
                # The server answered and refused: the credentials are wrong.
                raise OrviboAuthError(f"Login rejected (status {resp.get('status')})")
            raise OrviboApiError("Login timed out")
        return client

    def validate_credentials(self) -> bool:
        client = self._open_session()
        client.disconnect()
        return True

    def get_position(self) -> int | None:
        client = self._open_session()
        try:
            return client.query_position()
        finally:
            client.disconnect()

    def _control(self, order: str, value1: int) -> None:
        client = self._open_session()
        try:
            ok, replies = client.send_control(order, value1)
        finally:
            client.disconnect()
        if not ok:
            # Commands reach the motor even when the cloud's reply doesn't look
            # like a confirmation, so warn instead of failing the action.
            _LOGGER.warning(
                "Orvibo cloud did not confirm '%s' (%s); replies: %s",
                order, value1,
                [{k: r.get(k) for k in ("cmd", "status", "serial", "value1")} for r in replies] or "none",
            )

    def stop_curtain(self) -> None:
        self._control("stop", 0)

    def set_position(self, position: int) -> None:
        """Move to a device position (0-100). Open and close are 100 and 0."""
        self._control("open", max(0, min(100, position)))
