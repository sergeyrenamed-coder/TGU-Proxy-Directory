#!/usr/bin/env python3
"""Collect and normalize public proxy feeds for TGU. Standard library only."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import re
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

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


def socks_reply_address(reader: asyncio.StreamReader, atyp: int) -> asyncio.Future:
    async def read_address() -> None:
        if atyp == 1:
            await reader.readexactly(4)
        elif atyp == 3:
            size = (await reader.readexactly(1))[0]
            await reader.readexactly(size)
        elif atyp == 4:
            await reader.readexactly(16)
        else:
            raise ValueError("Invalid SOCKS5 address type.")
        await reader.readexactly(2)
    return asyncio.create_task(read_address())


async def test_socks5_candidate(host: str, port: int, timeout: float = 3.6) -> bool:
    writer = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        greeting = await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
        if greeting != b"\x05\x00":
            return False
        target_ip = ipaddress.ip_address(TELEGRAM_DC[0]).packed
        request = b"\x05\x01\x00\x01" + target_ip + TELEGRAM_DC[1].to_bytes(2, "big")
        writer.write(request)
        await writer.drain()
        head = await asyncio.wait_for(reader.readexactly(4), timeout=timeout)
        if head[0] != 5 or head[1] != 0:
            return False
        await asyncio.wait_for(socks_reply_address(reader, head[3]), timeout=timeout)
        return True
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError):
        return False
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass


async def test_socks_batch(candidates: list[tuple[str, int]], limit: int) -> list[tuple[str, int]]:
    semaphore = asyncio.Semaphore(18)
    async def run_one(candidate: tuple[str, int]) -> tuple[tuple[str, int], bool]:
        async with semaphore:
            return candidate, await test_socks5_candidate(*candidate)
    results = await asyncio.gather(*(run_one(x) for x in candidates[:limit]))
    return [candidate for candidate, ok in results if ok]


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
        catalog.metrics[source_id]["fetched"] = len(candidates)
        working = asyncio.run(test_socks_batch(candidates, socks_limit))
        for host, port in working:
            # add_socks5 increments fetched; restore the feed size below for meaningful source metrics.
            catalog.add_socks5(host, port, source_id)
        catalog.metrics[source_id]["fetched"] = len(candidates)
        catalog.source_ok(source_id, started)
        catalog.metrics[source_id]["tested"] = min(len(candidates), socks_limit)
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


def preserve_failed_sources(catalog: Catalog, previous: list[dict[str, Any]]) -> None:
    if not catalog.failed:
        return
    for item in previous:
        if not isinstance(item, dict):
            continue
        if not set(item.get("sources") or []).intersection(catalog.failed):
            continue
        copied = dict(item)
        copied["stale"] = True
        key = copied.get("id") or stable_id(copied.get("type"), copied.get("server"), copied.get("port"), copied.get("secret", ""))
        catalog.records.setdefault(key, copied)


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

    preserve_failed_sources(catalog, previous)
    now = utc_now()
    proxies = list(catalog.records.values())
    proxies.sort(key=lambda p: (
        0 if p.get("type") == "MTProto" else 1,
        bool(p.get("stale")),
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
        "counts": {"total": len(proxies), "mtproto": mt_count, "socks5": socks_count, "stale": stale_count},
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
            "stale": stale_count, "unsupported": catalog.unsupported,
        },
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
    parser.add_argument("--socks-limit", type=int, default=100, help="Maximum monosans SOCKS5 candidates to test per run (default: 100).")
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
        "sources": [{
            "id": s["id"], "status": s["status"], "fetched": s["fetched"],
            "usable": s["usable"], "unsupported": s["unsupported"], "error": s["error"]
        } for s in stats["sources"]],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
