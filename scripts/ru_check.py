#!/usr/bin/env python3
"""Prepare a bounded RU probe batch and safely merge results into GitHub JSON."""
import argparse
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

ID_RE = re.compile(r"^[a-f0-9]{20}$")
MAX_BATCH = 32

def utc_now():
    return datetime.now(timezone.utc)

def iso(value):
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")

def parse_time(value):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp if stamp.tzinfo else None
    except (ValueError, AttributeError, TypeError):
        return None

def read(path):
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Expected JSON object")
    return result

def save(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def candidates(catalog):
    if catalog.get("schema_version") != 1 or not isinstance(catalog.get("proxies"), list):
        raise ValueError("Unexpected catalog schema")
    prepared, seen = [], set()
    for proxy in catalog["proxies"]:
        if not isinstance(proxy, dict):
            continue
        pid = proxy.get("id")
        if not isinstance(pid, str) or not ID_RE.fullmatch(pid) or pid in seen:
            continue
        if proxy.get("type") not in ("SOCKS5", "MTProto") or proxy.get("probe_status") != "working":
            continue
        seen.add(pid)
        entry = {
            "id": pid, "type": proxy["type"], "server": proxy.get("server"),
            "port": proxy.get("port"),
        }
        if proxy["type"] == "MTProto":
            entry["secret"] = proxy.get("secret")
        prepared.append(entry)
    return prepared

def prepare(catalog, health, count=MAX_BATCH):
    entries = candidates(catalog)
    if not entries:
        raise ValueError("No eligible proxies")
    if not 1 <= count <= MAX_BATCH:
        raise ValueError("Batch count must be 1..32")
    start = int(health.get("cursor", 0)) % len(entries)
    selected = [entries[(start + pos) % len(entries)] for pos in range(min(count, len(entries)))]
    return {"schema_version": 1, "proxies": selected}

def merge(catalog, health, request, answer, now=None):
    now = now or utc_now()
    if answer.get("ok") is not True or answer.get("schema_version") != 1 or answer.get("checker") != "yandex_cloud_ru":
        raise ValueError("Invalid RU checker reply")
    selected = {p["id"]: p["type"] for p in request["proxies"]}
    reported = answer.get("results")
    if not selected or not isinstance(reported, list) or len(reported) != len(selected):
        raise ValueError("Missing RU results")
    present = set()
    for record in reported:
        if not isinstance(record, dict):
            raise ValueError("Malformed RU result")
        pid = record.get("proxy_id")
        if pid in present or pid not in selected or record.get("type") != selected[pid]:
            raise ValueError("Unknown or duplicate RU proxy result")
        present.add(pid)
        if record.get("status") not in ("working", "failed"):
            raise ValueError("Bad RU status")
        delay = record.get("latency_ms")
        if delay is not None and (type(delay) is not int or not 0 <= delay <= 60000):
            raise ValueError("Bad RU latency")
        checked = parse_time(record.get("checked_at"))
        if checked is None or abs((now - checked).total_seconds()) > 3600:
            raise ValueError("Stale RU result")
    if present != set(selected):
        raise ValueError("Partial RU response")

    available = {entry["id"] for entry in candidates(catalog)}
    cutoff = now - timedelta(hours=24)
    previous = {
        row["proxy_id"]: row for row in health.get("checks", [])
        if isinstance(row, dict) and row.get("proxy_id") in available
        and (parse_time(row.get("checked_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
    }
    for row in reported:
        previous[row["proxy_id"]] = {
            "proxy_id": row["proxy_id"],
            "type": row["type"],
            "status": row["status"],
            "latency_ms": row["latency_ms"] if row["status"] == "working" else None,
            "failure_code": row.get("failure_code", "unknown") if row["status"] == "failed" else "none",
            "checked_at": row["checked_at"],
        }
    checks = sorted(previous.values(), key=lambda r: r["proxy_id"])
    successes = sum(r["status"] == "working" for r in checks)
    size = len(available)
    return {
        "schema_version": 1, "service": "TGU RU Checker",
        "checker": "yandex_cloud_ru", "generated_at": iso(now),
        "status": "active", "cursor": int(health.get("cursor", 0)) + len(selected),
        "ttl_hours": 24,
        "totals": {
            "catalog": size, "checked": len(checks), "working": successes,
            "failed": len(checks) - successes,
            "coverage_percent": round(100 * len(checks) / size, 1) if size else 0,
        },
        "checks": checks,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "merge"])
    parser.add_argument("--catalog", default="data/proxies.json")
    parser.add_argument("--health", default="data/ru-health.json")
    parser.add_argument("--request", required=True)
    parser.add_argument("--response")
    parser.add_argument("--size", type=int, default=MAX_BATCH)
    args = parser.parse_args()
    catalog, health = read(args.catalog), read(args.health)
    if args.command == "prepare":
        request = prepare(catalog, health, args.size)
        save(args.request, request)
        print("RU Checker: selected", len(request["proxies"]), "proxies.")
    else:
        if not args.response:
            parser.error("--response is required for merge")
        output = merge(catalog, health, read(args.request), read(args.response))
        save(args.health, output)
        save("docs/data/ru-health.json", output)
        print("RU Checker:", output["totals"]["working"], "working /", output["totals"]["checked"], "checked.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
