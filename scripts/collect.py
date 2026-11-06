#!/usr/bin/env python3
"""Collect and normalize public proxy feeds for TGU. Standard library only."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DATA_DIR = ROOT / "docs" / "data"
USER_AGENT = "TGU-Proxy-Directory/1.0 (+https://github.com/sergeyrenamed-coder/TGU-Proxy-Directory)"
TELEGRAM_DC = ("149.154.167.51", 443)
RAW_RADIKAL_INDEX = "https://raw.githubusercontent.com/0xRadikal/Free-Telegram-Proxy/main/index.json"
RADIKAL_FALLBACK = {
    "stable": "https://raw.githubusercontent.com/0xRadikal/Free-Telegram-Proxy/main/stable/proxies.json",
    "verified": "https://raw.githubusercontent.com/0xRadikal/Free-Telegram-Proxy/main/verified/proxies.json",
}
SOURCES = [
    {"id": "radikal-stable", "name": "0xRadikal · Stable", "kind": "MTProto", "url": RADIKAL_FALLBACK["stable"]},
    {"id": "radikal-verified", "name": "0xRadikal · Verified", "kind": "MTProto", "url": RADIKAL_FALLBACK["verified"]},
    {"id": "tgmtproxy", "name": "tgmtproxy", "kind": "MTProto", "url": "https://raw.githubusercontent.com/tgmtproxy/mtproxy/main/proxies.json"},
    {"id": "freedom-checker", "name": "Freedom Checker", "kind": "MTProto / SOCKS5", "url": "https://freedomchecker.ateo.digital/api/snapshot.json"},
    {"id": "monosans-socks5", "name": "monosans · SOCKS5", "kind": "SOCKS5", "url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt"},
]
HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
MAX_DOWNLOAD = 8 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_bytes(url: str, timeout: int = 18) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("Only HTTPS source URLs are allowed.")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*", "Cache-Control": "no-cache"})
    with urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        if not final_url.startswith("https://"):
            raise ValueError("Source redirected away from HTTPS.")
        body = response.read(MAX_DOWNLOAD + 1)
        if len(body) > MAX_DOWNLOAD:
            raise ValueError("Source response exceeded the 8 MiB safety limit.")
        if not body:
            raise ValueError("Source returned an empty response.")
        return body


def fetch_json(url: str) -> Any:
    return json.loads(fetch_bytes(url).decode("utf-8-sig"))


def valid_host(host: Any) -> str | None:
    if not isinstance(host, (str, int)):
        return None
    value = str(host).strip().strip("[]")
    if not value or len(value) > 253 or any(ch.isspace() for ch in value) or not HOST_RE.fullmatch(value):
        return None
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        if value.startswith(".") or value.endswith(".") or ".." in value:
            return None
        labels = value.split(".")
        if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels):
            return None
        return value.lower()


def valid_port(port: Any) -> int | None:
    try:
        value = int(port)
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 65535 else None


def stable_id(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]


def parse_link(link: str) -> dict[str, Any] | None:
    try:
        uri = urlparse(link.strip())
        query = parse_qs(uri.query)
        host = (query.get("server") or [None])[0]
        port = (query.get("port") or [None])[0]
        secret = (query.get("secret") or [""])[0]
        if not host or not port:
            return None
        return {"server": host, "port": port, "secret": secret, "type": "MTProto" if secret else "SOCKS5"}
    except Exception:
        return None


def mtproto_supported(secret: str) -> bool:
    value = str(secret or "").strip().lower()
    if not value or not HEX_RE.fullmatch(value) or len(value) % 2:
        return False
    if value.startswith("ee"):
        return False  # FakeTLS/WEB is intentionally excluded by the current TGU engine.
    return len(value) == 32 or (value.startswith("dd") and len(value) == 34)


def mtproto_secret_bytes(value: str) -> bytes:
    """Return the 16-byte key while validating the supported classic/dd formats."""
    normalized = str(value or "").strip().lower()
    if normalized.startswith("dd") and len(normalized) == 34:
        normalized = normalized[2:]
    if len(normalized) != 32 or not HEX_RE.fullmatch(normalized):
        raise ValueError("Unsupported MTProto secret.")
    secret = bytes.fromhex(normalized)
    if len(secret) != 16:
        raise ValueError("MTProto secret must be exactly 16 bytes.")
    return secret


def mtproto_uses_random_padding(value: str) -> bool:
    """TDLib's dd-prefixed secret selects padded-intermediate framing."""
    normalized = str(value or "").strip().lower()
    return normalized.startswith("dd") and len(normalized) == 34


def number_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        result = float(value)
        return result if result >= 0 else None
    except (TypeError, ValueError):
        return None


class Catalog:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.metrics: dict[str, dict[str, Any]] = {}
        self.successful: set[str] = set()
        self.failed: set[str] = set()
        self.unsupported = 0

    def start_source(self, source: dict[str, str]) -> None:
        self.metrics[source["id"]] = {
            "id": source["id"], "name": source["name"], "kind": source["kind"],
            "url": source["url"], "status": "pending", "fetched": 0, "usable": 0,
            "unsupported": 0, "elapsed_ms": 0, "error": None,
        }

    def add_mtproto(self, record: dict[str, Any], source_id: str, tier: str = "external") -> bool:
        metric = self.metrics[source_id]
        metric["fetched"] += 1
        host = valid_host(record.get("server") or record.get("host") or record.get("ip") or record.get("address"))
        port = valid_port(record.get("port"))
        secret = str(record.get("secret") or record.get("mtproto_secret") or "").strip().lower()
        hint = str(record.get("type") or record.get("protocol_type") or record.get("protocol") or "").lower()
        if not host or not port or not secret:
            metric["unsupported"] += 1
            self.unsupported += 1
            return False
        if not mtproto_supported(secret):
            metric["unsupported"] += 1
            self.unsupported += 1
            return False
        key = stable_id("mtproto", host.lower(), port, secret)
        item = {
            "id": key, "type": "MTProto", "protocol": "MTProto",
            "server": host, "port": port, "secret": secret, "enabled": True,
            "country": str(record.get("country") or "").upper()[:2],
            "rtt_ms": number_or_none(record.get("rtt_ms") or record.get("latency_ms")),
            "uptime": number_or_none(record.get("uptime")),
            "checks": int(record.get("checks") or 0) if str(record.get("checks") or "0").isdigit() else 0,
            "source_tier": tier, "sources": [source_id], "last_check": str(record.get("last_check") or record.get("last_checked") or ""),
            "stale": False,
        }
        self._merge(key, item)
        metric["usable"] += 1
        return True

    def add_socks5(self, host_value: Any, port_value: Any, source_id: str, probe: str = "socks5-connect-to-telegram-dc") -> bool:
        metric = self.metrics[source_id]
        metric["fetched"] += 1
        host, port = valid_host(host_value), valid_port(port_value)
        if not host or not port:
            metric["unsupported"] += 1
            self.unsupported += 1
            return False
        key = stable_id("socks5", host.lower(), port)
        item = {
            "id": key, "type": "SOCKS5", "protocol": "SOCKS5",
            "server": host, "port": port, "username": "", "password": "",
            "enabled": True, "country": "", "rtt_ms": None, "uptime": None,
            "sources": [source_id], "probe": probe, "stale": False,
        }
        self._merge(key, item)
        metric["usable"] += 1
        return True

    def _merge(self, key: str, item: dict[str, Any]) -> None:
        old = self.records.get(key)
        if old is None:
            self.records[key] = item
            return
        old_sources = set(old.get("sources") or [])
        old_sources.update(item.get("sources") or [])
        old["sources"] = sorted(old_sources)
        old["stale"] = bool(old.get("stale")) and bool(item.get("stale"))
        # Prefer the stronger tier metadata when an identical proxy appears in multiple feeds.
        rank = {"stable": 0, "verified": 1, "external": 2}
        if rank.get(item.get("source_tier"), 3) < rank.get(old.get("source_tier"), 3):
            for field in ("source_tier", "country", "rtt_ms", "uptime", "checks", "last_check"):
                if item.get(field) not in (None, "", 0):
                    old[field] = item[field]
        elif old.get("rtt_ms") is None and item.get("rtt_ms") is not None:
            old["rtt_ms"] = item["rtt_ms"]

    def source_ok(self, source_id: str, started: float) -> None:
        self.successful.add(source_id)
        m = self.metrics[source_id]
        m["status"] = "ok"
        m["elapsed_ms"] = int((time.monotonic() - started) * 1000)

    def source_fail(self, source_id: str, started: float, exc: Exception) -> None:
        self.failed.add(source_id)
        m = self.metrics[source_id]
        m["status"] = "error"
        m["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        m["error"] = (str(exc).replace("\n", " ")[:220] or exc.__class__.__name__)


def get_radikal_tier_url(index: dict[str, Any], tier: str) -> str:
    try:
        url = index["tiers"][tier]["files"]["proxies.json"]
        if not isinstance(url, str) or not url.startswith("https://raw.githubusercontent.com/0xRadikal/Free-Telegram-Proxy/"):
            raise ValueError("Unexpected 0xRadikal tier URL.")
        return url
    except (KeyError, TypeError):
        return RADIKAL_FALLBACK[tier]


def collect_radikal(catalog: Catalog, tier: str) -> None:
    source_id = "radikal-" + tier
    source = next(x for x in SOURCES if x["id"] == source_id)
    catalog.start_source(source)
    started = time.monotonic()
    try:
        index = fetch_json(RAW_RADIKAL_INDEX)
        url = get_radikal_tier_url(index, tier)
        payload = fetch_json(url)
        records = payload if isinstance(payload, list) else payload.get("proxies", [])
        if not isinstance(records, list):
            raise ValueError("Unexpected 0xRadikal proxy schema.")
        for item in records:
            if isinstance(item, dict):
                catalog.add_mtproto(item, source_id, tier=tier)
        catalog.source_ok(source_id, started)
    except Exception as exc:
        catalog.source_fail(source_id, started, exc)


def walk_freedom_records(value: Any):
    if isinstance(value, dict):
        if value.get("enabled", True) is not False:
            yield value
        for child in value.values():
            yield from walk_freedom_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_freedom_records(child)


def collect_freedom(catalog: Catalog) -> None:
    source_id = "freedom-checker"
    source = next(x for x in SOURCES if x["id"] == source_id)
    catalog.start_source(source)
    started = time.monotonic()
    try:
        payload = fetch_json(source["url"])
        seen: set[str] = set()
        for record in walk_freedom_records(payload):
            link = record.get("proxy_link") or record.get("link") or record.get("url")
            parsed = parse_link(link) if isinstance(link, str) and ("t.me/" in link or link.lower().startswith("tg://")) else None
            candidate = parsed or record
            marker = json.dumps({k: candidate.get(k) for k in ("server", "host", "ip", "port", "secret")}, sort_keys=True, ensure_ascii=False)
            if marker in seen:
                continue
            seen.add(marker)
            hint = str(candidate.get("type") or candidate.get("protocol_type") or "").lower()
            if "socks" in hint:
                catalog.add_socks5(candidate.get("server") or candidate.get("host") or candidate.get("ip"), candidate.get("port"), source_id, probe="source-reported")
            else:
                catalog.add_mtproto(candidate, source_id, tier="external")
        catalog.source_ok(source_id, started)
    except Exception as exc:
        catalog.source_fail(source_id, started, exc)


def recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        part = sock.recv(size - len(chunks))
        if not part:
            raise OSError("Remote endpoint closed the connection.")
        chunks.extend(part)
    return bytes(chunks)


def connect_public_socket(host: str, port: int, timeout: float) -> socket.socket:
    """Resolve once and connect only to a globally routable address (no private-IP probing)."""
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    last_error: Exception | None = None
    for family, socktype, proto, _canonname, sockaddr in addresses:
        try:
            address = ipaddress.ip_address(sockaddr[0])
            if not address.is_global:
                continue
            connection = socket.socket(family, socktype, proto)
            connection.settimeout(timeout)
            try:
                connection.connect(sockaddr)
                return connection
            except Exception:
                connection.close()
                raise
        except Exception as exc:
            last_error = exc
    if last_error:
        raise OSError("No reachable public address for proxy host.") from last_error
    raise OSError("Proxy host did not resolve to a public IP address.")


def probe_mtproto_candidate(host: str, port: int, secret_hex: str, timeout: float = 4.0) -> tuple[bool, int, str]:
    """Verify a classic/dd MTProxy with the same framing and req_pq flow as TDLib."""
    started = time.monotonic()
    connection: socket.socket | None = None
    stage = "secret"
    try:
        secret = mtproto_secret_bytes(secret_hex)
        padded = mtproto_uses_random_padding(secret_hex)
        relay_init = bytearray(os.urandom(64))
        reserved_starts = {
            b"HEAD", b"POST", b"GET ", b"OPTI", b"PVrG",
            b"\xee\xee\xee\xee", b"\xdd\xdd\xdd\xdd",
            b"\x16\x03\x01\x02",
        }
        for _ in range(256):
            if (
                relay_init[0] != 0xEF
                and bytes(relay_init[:4]) not in reserved_starts
                and relay_init[4:8] != b"\x00\x00\x00\x00"
            ):
                break
            relay_init = bytearray(os.urandom(64))
        else:
            raise OSError("Could not generate an acceptable obfuscated header.")

        # TDLib chooses transport framing from the secret marker:
        # classic 16-byte secrets use intermediate (eeee); dd secrets use padded-intermediate.
        transport_tag = b"\xdd\xdd\xdd\xdd" if padded else b"\xee\xee\xee\xee"
        relay_init[56:60] = transport_tag
        relay_init[60:62] = struct.pack("<h", 2)

        reversed_header = bytes(relay_init[::-1])
        encrypt_key = hashlib.sha256(bytes(relay_init[8:40]) + secret).digest()
        encrypt_iv = bytes(relay_init[40:56])
        decrypt_key = hashlib.sha256(reversed_header[8:40] + secret).digest()
        decrypt_iv = reversed_header[40:56]

        outbound = Cipher(algorithms.AES(encrypt_key), modes.CTR(encrypt_iv)).encryptor()
        inbound = Cipher(algorithms.AES(decrypt_key), modes.CTR(decrypt_iv)).decryptor()
        encrypted_header = outbound.update(bytes(relay_init))
        wire_header = bytes(relay_init[:56]) + encrypted_header[56:64]

        nonce = os.urandom(16)
        message_id = (int(time.time() * (1 << 32)) // 4) * 4
        body = struct.pack("<I", 0xBE7E8EF1) + nonce
        request = struct.pack("<qqi", 0, message_id, len(body)) + body
        padding = os.urandom(secrets.randbelow(16)) if padded else b""
        frame = struct.pack("<I", len(request) + len(padding)) + request + padding

        stage = "TCP connect"
        connection = connect_public_socket(host, port, timeout)
        stage = "MTProto request"
        connection.sendall(wire_header + outbound.update(frame))

        stage = "MTProto response"
        encrypted_length = recv_exact(connection, 4)
        frame_length = struct.unpack("<I", inbound.update(encrypted_length))[0] & 0x7FFFFFFF
        if frame_length < 4 or frame_length > 4096:
            raise OSError("Invalid intermediate response frame length.")
        encrypted_response = recv_exact(connection, frame_length)
        response = inbound.update(encrypted_response)
        if frame_length == 4:
            transport_error = struct.unpack("<i", response)[0]
            raise OSError(f"Telegram transport error {transport_error}.")
        if len(response) < 40:
            raise OSError("MTProto response was too short.")
        if struct.unpack_from("<I", response, 20)[0] != 0x05162463:
            raise OSError("Telegram DC returned an unexpected MTProto constructor.")
        if response[24:40] != nonce:
            raise OSError("Telegram DC did not echo the MTProto test nonce.")
        elapsed_ms = max(1, int((time.monotonic() - started) * 1000))
        return True, elapsed_ms, "ok"
    except Exception as exc:
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        reason = f"{stage}:{type(exc).__name__}:{str(exc)[:100]}"
        return False, elapsed_ms, reason
    finally:
        if connection is not None:
            connection.close()


def probe_socks5_candidate(host: str, port: int, timeout: float = 3.6) -> tuple[bool, int, str]:
    """Verify a no-auth SOCKS5 tunnel to a real Telegram DC, not a generic website."""
    started = time.monotonic()
    connection: socket.socket | None = None
    stage = "TCP connect"
    try:
        connection = connect_public_socket(host, port, timeout)
        stage = "SOCKS5 negotiation"
        connection.sendall(b"\x05\x01\x00")
        if recv_exact(connection, 2) != b"\x05\x00":
            raise OSError("SOCKS5 endpoint rejected no-auth negotiation.")

        stage = "SOCKS5 CONNECT to Telegram DC"
        target_ip = ipaddress.ip_address(TELEGRAM_DC[0]).packed
        connection.sendall(b"\x05\x01\x00\x01" + target_ip + TELEGRAM_DC[1].to_bytes(2, "big"))
        reply = recv_exact(connection, 4)
        if reply[0] != 5 or reply[1] != 0:
            raise OSError("SOCKS5 CONNECT to Telegram DC failed.")
        if reply[3] == 1:
            recv_exact(connection, 4)
        elif reply[3] == 3:
            recv_exact(connection, recv_exact(connection, 1)[0])
        elif reply[3] == 4:
            recv_exact(connection, 16)
        else:
            raise OSError("SOCKS5 returned an invalid address type.")
        recv_exact(connection, 2)
        return True, max(1, int((time.monotonic() - started) * 1000)), "ok"
    except Exception as exc:
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        reason = f"{stage}:{type(exc).__name__}:{str(exc)[:100]}"
        return False, elapsed_ms, reason
    finally:
        if connection is not None:
            connection.close()


async def probe_catalog(catalog: Catalog, concurrency: int = 32) -> dict[str, int]:
    """Publish only endpoints that pass a live protocol-level Telegram connectivity probe."""
    candidates = list(catalog.records.items())
    semaphore = asyncio.Semaphore(max(1, concurrency))
    started = time.monotonic()

    for metric in catalog.metrics.values():
        metric["usable"] = 0
        metric["tested"] = 0
        metric["working"] = 0
        metric["failed_probes"] = 0

    async def run_one(key: str, item: dict[str, Any]):
        async with semaphore:
            kind = item.get("type")
            if kind == "MTProto":
                ok, latency, reason = await asyncio.to_thread(
                    probe_mtproto_candidate,
                    str(item["server"]), int(item["port"]), str(item["secret"]), 4.0,
                )
                probe_name = "mtproto-obfuscated2-req_pq_multi"
            elif kind == "SOCKS5":
                ok, latency, reason = await asyncio.to_thread(
                    probe_socks5_candidate, str(item["server"]), int(item["port"]), 3.6,
                )
                probe_name = "socks5-connect-telegram-dc"
            else:
                return key, item, False, 0, "unsupported", "unsupported:type"
            return key, item, ok, latency, probe_name, reason

    results = await asyncio.gather(
        *(run_one(key, item) for key, item in candidates),
        return_exceptions=True,
    )
    now = utc_now()
    working: dict[str, dict[str, Any]] = {}
    mtproto_count = 0
    socks_count = 0

    failure_reasons: dict[str, int] = {}
    for result in results:
        if isinstance(result, Exception):
            reason = f"unexpected:{type(result).__name__}"
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
            continue
        key, item, ok, latency, probe_name, reason = result
        source_ids = item.get("sources") or []
        for source_id in source_ids:
            metric = catalog.metrics.get(source_id)
            if metric is not None:
                metric["tested"] += 1
                if ok:
                    metric["working"] += 1
                    metric["usable"] += 1
                else:
                    metric["failed_probes"] += 1
                    reasons = metric.setdefault("failed_reasons", {})
                    reasons[reason] = reasons.get(reason, 0) + 1
        if not ok:
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
            continue

        item["enabled"] = True
        item["stale"] = False
        item["probe_status"] = "working"
        item["probe"] = probe_name
        item["rtt_ms"] = latency
        item["last_check"] = now
        item["last_seen"] = now
        item["checks"] = max(1, int(item.get("checks") or 0))
        working[key] = item
        if item.get("type") == "MTProto":
            mtproto_count += 1
        elif item.get("type") == "SOCKS5":
            socks_count += 1

    catalog.records = working
    tested = len(candidates)
    passed = len(working)
    return {
        "tested": tested,
        "working": passed,
        "rejected": tested - passed,
        "mtproto_working": mtproto_count,
        "socks5_working": socks_count,
        "failure_reasons": dict(sorted(failure_reasons.items(), key=lambda pair: (-pair[1], pair[0]))[:20]),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def collect_monosans(catalog: Catalog, socks_limit: int) -> None:
    source_id = "monosans-socks5"
    source = next(x for x in SOURCES if x["id"] == source_id)
    catalog.start_source(source)
    started = time.monotonic()
    try:
        text = fetch_bytes(source["url"]).decode("utf-8-sig", errors="replace")
        candidates: list[tuple[str, int]] = []
        seen: set[tuple[str, int]] = set()
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("socks5://"):
                line = line[10:]
            if "@" in line:
                line = line.rsplit("@", 1)[1]
            if line.count(":") != 1:
                continue
            host, port_text = line.rsplit(":", 1)
            valid = valid_host(host)
            port = valid_port(port_text)
            if valid and port and (valid, port) not in seen:
                seen.add((valid, port))
                candidates.append((valid, port))
        selected = candidates[:socks_limit]
        for host, port in selected:
            catalog.add_socks5(host, port, source_id, probe="pending-active-probe")
        catalog.metrics[source_id]["fetched"] = len(candidates)
        catalog.metrics[source_id]["limited"] = max(0, len(candidates) - len(selected))
        catalog.source_ok(source_id, started)
    except Exception as exc:
        catalog.source_fail(source_id, started, exc)


def collect_tgmtproxy(catalog: Catalog) -> None:
    source_id = "tgmtproxy"
    source = next(x for x in SOURCES if x["id"] == source_id)
    catalog.start_source(source)
    started = time.monotonic()
    try:
        payload = fetch_json(source["url"])
        records = payload.get("proxies", []) if isinstance(payload, dict) else []
        if not isinstance(records, list):
            raise ValueError("Unexpected tgmtproxy JSON schema.")
        for item in records:
            if isinstance(item, dict):
                catalog.add_mtproto(item, source_id, tier="external")
        catalog.source_ok(source_id, started)
    except Exception as exc:
        catalog.source_fail(source_id, started, exc)


def load_previous() -> list[dict[str, Any]]:
    path = DATA_DIR / "proxies.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("proxies", []) if isinstance(payload, dict) else []
    except (OSError, json.JSONDecodeError):
        return []


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def run_collection(socks_limit: int = 100) -> dict[str, Any]:
    previous = load_previous()
    catalog = Catalog()
    for source in SOURCES:
        catalog.start_source(source)
    # collect_* starts its own source metrics so source timings include fetch and parsing.
    collect_radikal(catalog, "stable")
    collect_radikal(catalog, "verified")
    collect_tgmtproxy(catalog)
    collect_freedom(catalog)
    collect_monosans(catalog, socks_limit)

    if not catalog.successful:
        errors = "; ".join((m["name"] + ": " + str(m["error"])) for m in catalog.metrics.values())
        raise RuntimeError("Every proxy source failed; previous published data was left untouched. " + errors)

    probe_stats = asyncio.run(probe_catalog(catalog))
    now = utc_now()
    proxies = list(catalog.records.values())
    proxies.sort(key=lambda p: (
        0 if p.get("type") == "MTProto" else 1,
        (p.get("rtt_ms") if p.get("rtt_ms") is not None else 999999),
        -(p.get("uptime") if p.get("uptime") is not None else 0),
        p.get("server", ""),
        p.get("port", 0),
    ))
    for p in proxies:
        p["last_seen"] = p.get("last_seen") or now

    mt_count = sum(1 for p in proxies if p.get("type") == "MTProto")
    socks_count = sum(1 for p in proxies if p.get("type") == "SOCKS5")
    stale_count = sum(1 for p in proxies if p.get("stale"))
    proxy_payload = {
        "schema_version": 1, "service": "TGU Proxy Directory",
        "generated_at": now, "generated_unix": int(time.time()),
        "counts": {
            "total": len(proxies), "working": len(proxies), "mtproto": mt_count,
            "socks5": socks_count, "stale": 0, "tested": probe_stats["tested"],
            "rejected": probe_stats["rejected"],
        },
        "proxies": proxies,
    }
    source_rows = list(catalog.metrics.values())
    for row in source_rows:
        row["checked_at"] = now
        if row.get("status") == "pending":
            row["status"] = "unknown"
    stats_payload = {
        "schema_version": 1, "generated_at": now,
        "overall_status": "partial" if catalog.failed else "ok",
        "totals": {
            "total": len(proxies), "mtproto": mt_count, "socks5": socks_count,
            "stale": 0, "unsupported": catalog.unsupported,
            "tested": probe_stats["tested"], "working": probe_stats["working"],
            "rejected": probe_stats["rejected"],
        },
        "probe": probe_stats,
        "sources": source_rows,
    }
    source_payload = [{"id": s["id"], "name": s["name"], "kind": s["kind"], "url": s["url"]} for s in SOURCES]

    for directory in (DATA_DIR, DOCS_DATA_DIR):
        write_json(directory / "proxies.json", proxy_payload)
        write_json(directory / "stats.json", stats_payload)
        write_json(directory / "sources.json", source_payload)
    return stats_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect and publish the TGU-compatible proxy catalog.")
    parser.add_argument("--socks-limit", type=int, default=1000, help="Maximum monosans SOCKS5 candidates to include in the live probe (default: 1000).")
    args = parser.parse_args()
    if not 0 <= args.socks_limit <= 1000:
        parser.error("--socks-limit must be between 0 and 1000")
    try:
        stats = run_collection(args.socks_limit)
    except Exception as exc:
        print("COLLECTION FAILED: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps({
        "generated_at": stats["generated_at"],
        "overall_status": stats["overall_status"],
        "totals": stats["totals"],
        "probe": stats["probe"],
        "sources": [{
            "id": s["id"], "status": s["status"], "fetched": s["fetched"],
            "usable": s["usable"], "unsupported": s["unsupported"], "error": s["error"]
        } for s in stats["sources"]],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
