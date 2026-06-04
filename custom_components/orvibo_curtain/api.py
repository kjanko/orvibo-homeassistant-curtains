"""Orvibo cloud protocol client for the Home Assistant integration.

Handles:
- REST API discovery (OAuth login → familyId + host)
- Binary socket protocol (TLS mutual-auth, AES-ECB encrypted JSON commands)
"""

from __future__ import annotations

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


class OrviboApiError(Exception):
    """Raised when communication with the Orvibo cloud fails."""


# ---------------------------------------------------------------------------
# REST API — only used to discover familyId + validate host
# ---------------------------------------------------------------------------


def discover_family(
    email: str,
    password_md5: str,
    hosts: list[str],
) -> tuple[str, str]:
    """Find the correct regional host and familyId for this account.

    Returns (host, family_id) or raises OrviboApiError.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for host in hosts:
        try:
            # OAuth login
            params = parse.urlencode({"userName": email, "type": "0", "password": password_md5})
            url = f"https://{host}/getOauthToken?{params}"
            req = request.Request(url, method="GET")
            with request.urlopen(req, timeout=15, context=ctx) as resp:
                oauth = json.loads(resp.read().decode())
        except Exception as exc:
            _LOGGER.debug("OAuth failed on %s: %s", host, exc)
            continue

        # Response is {"data": {"access_token": ..., "user_id": ...}, "status": 0}
        data = oauth.get("data", oauth)
        token = data.get("access_token") or data.get("accessToken")
        user_id = data.get("user_id") or data.get("userId")
        if not token or not user_id:
            continue

        # Get family list
        try:
            body = {
                "accessToken": token,
                "userId": user_id,
                "timestamp": str(int(time.time() * 1000)),
                "random": str(random.randint(100000, 999999)),
            }
            sign_items = [f"{k}={body[k]}" for k in sorted(body) if body[k] is not None]
            sign_items.append(f"key={_HMAC_SECRET}")
            body["sign"] = hmac.new(
                _HMAC_SECRET.encode(), "&".join(sign_items).encode(), hashlib.sha256
            ).hexdigest().upper()

            req = request.Request(
                f"https://{host}/v2/family/statistics/users",
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(req, timeout=15, context=ctx) as resp:
                family_resp = json.loads(resp.read().decode())
        except Exception as exc:
            _LOGGER.debug("Family query failed on %s: %s", host, exc)
            # Even without family list, we can return the host — user's familyId
            # is probably derivable from their account
            continue

        families = family_resp.get("data", [])
        if families and isinstance(families, list):
            family_id = families[0].get("familyId", "")
            if family_id:
                _LOGGER.info("Discovered host=%s familyId=%s", host, family_id)
                return host, family_id

    raise OrviboApiError(
        "Could not connect to Orvibo cloud. Check email and password."
    )


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
    ) -> None:
        self._host = host
        self._port = port
        self._email = email
        self._password_md5 = password_md5
        self._family_id = family_id
        self._device_id = device_id

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
            "ver": "5.2.5.301",
            "debugInfo": "Android_ZhiJia365_32_5.2.5.301",
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

    def login(self) -> bool:
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
            "ver": "5.2.5.301",
            "debugInfo": "Android_ZhiJia365_32_5.2.5.301",
        }
        self._sock.sendall(self._build_packet(payload))
        resp = self._recv(timeout=10)
        return bool(resp and resp.get("status") == 0)

    def query_position(self) -> int | None:
        payload = {
            "familyId": self._family_id,
            "cmd": 230,
            "serial": self._next_serial(),
            "clientType": 1,
            "uniSerial": int(time.time() * 1000),
            "serverRecord": False,
            "ver": "5.2.5.301",
            "debugInfo": "Android_ZhiJia365_32_5.2.5.301",
        }
        self._sock.sendall(self._build_packet(payload))
        deadline = time.time() + 5
        while time.time() < deadline:
            resp = self._recv(timeout=2)
            if not resp:
                continue
            if resp.get("cmd") == 42 and "value1" in resp:
                return max(0, min(100, int(resp["value1"])))
        return None

    def send_control(self, order: str, value1: int) -> bool:
        payload = {
            "uid": self._device_id,
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
            "ver": "5.2.5.301",
            "debugInfo": "Android_ZhiJia365_32_5.2.5.301",
        }
        self._sock.sendall(self._build_packet(payload))
        resp = self._recv(timeout=10)
        if resp:
            if resp.get("status") == 0:
                return True
            if resp.get("cmd") == 42:
                return True
        return False


# ---------------------------------------------------------------------------
# High-level API used by the HA coordinator
# ---------------------------------------------------------------------------


class OrviboCurtainApi:
    """Manages short-lived sessions to the Orvibo binary protocol."""

    def __init__(
        self, host: str, email: str, password_md5: str, family_id: str, device_id: str
    ) -> None:
        self._host = host
        self._email = email
        self._password_md5 = password_md5
        self._family_id = family_id
        self._device_id = device_id

    def _open_session(self) -> OrviboClient:
        from .const import DEFAULT_PORT
        client = OrviboClient(
            host=self._host,
            port=DEFAULT_PORT,
            email=self._email,
            password_md5=self._password_md5,
            family_id=self._family_id,
            device_id=self._device_id,
        )
        client.connect()
        if not client.handshake():
            client.disconnect()
            raise OrviboApiError("Handshake failed")
        if not client.login():
            client.disconnect()
            raise OrviboApiError("Login failed")
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

    def open_curtain(self) -> bool:
        client = self._open_session()
        try:
            return client.send_control("open", 100)
        finally:
            client.disconnect()

    def close_curtain(self) -> bool:
        client = self._open_session()
        try:
            return client.send_control("open", 0)
        finally:
            client.disconnect()

    def stop_curtain(self) -> bool:
        client = self._open_session()
        try:
            return client.send_control("stop", 0)
        finally:
            client.disconnect()

    def set_position(self, position: int) -> bool:
        client = self._open_session()
        try:
            return client.send_control("open", max(0, min(100, position)))
        finally:
            client.disconnect()
