#!/usr/bin/env python3
"""One-off 400-endpoint RU cloud sample. No repository writes or publication.

Selects 400 distinct global-working proxies, including the manually tested
MTProto proxy, and probes them in <=32-item authenticated Yandex calls.
The report intentionally excludes MTProto secrets and authorization headers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from scripts.ru_check import candidates, merge, read, save, utc_now, iso

ROOT = Path(__file__).resolve().parents[1]
PINNED_ID = "400e228c511c694acb13"
MAX_BATCH = 32
MAX_COUNT = 400
RESPONSE_LIMIT = 1024 * 1024


def choose_sample(catalog: dict, count: int) -> list[dict]:
    if not 1 <= count <= MAX_COUNT:
        raise ValueError("Expected 1..400 selected proxies.")
    items = candidates(catalog)
    if len(items) < count:
        raise ValueError(f"Only {len(items)} eligible proxies; requested {count}.")
    pinned = [x for x in items if x["id"] == PINNED_ID]
    remaining = [x for x in items if x["id"] != PINNED_ID]
    # Stable unbiased-by-feed ordering; no duplicate hosts are selected by ID.
    remaining.sort(key=lambda x: hashlib.sha256(x["id"].encode("ascii")).digest())
    selection = (pinned[:1] + remaining)[:count]
    assert len({item["id"] for item in selection}) == len(selection) == count
    return selection


def validate_url(value: str) -> None:
    parts = urlsplit(value)
    if (parts.scheme != "https" or parts.hostname != "functions.yandexcloud.net"
            or not parts.path.startswith("/") or parts.path == "/"
            or parts.username or parts.password or parts.query or parts.fragment
            or parts.port is not None):
        raise ValueError("Expected a private Yandex Cloud Functions HTTPS endpoint.")


def call_yandex(url: str, token: str, batch: list[dict]) -> dict:
    # No secret is printed, interpolated in exception text or included in reports.
    validate_url(url)
    if not token:
        raise ValueError("Missing TGU_RU_FUNCTION_API_KEY.")
    payload = json.dumps({"schema_version": 1, "proxies": batch}, separators=(",", ":")).encode("utf-8")
    if len(payload) > 65536 or not 1 <= len(batch) <= MAX_BATCH:
        raise ValueError("Yandex Cloud batch exceeded the safety limit.")
    request = Request(
        url, data=payload, method="POST",
        headers={"Authorization": "Api-Key " + token, "Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            with urlopen(request, timeout=100) as response:
                if response.status != 200:
                    raise ValueError(f"Unexpected Yandex status {response.status}.")
                raw = response.read(RESPONSE_LIMIT + 1)
                if len(raw) > RESPONSE_LIMIT:
                    raise ValueError("RU function response is too large.")
                answer = json.loads(raw.decode("utf-8"))
                if not isinstance(answer, dict):
                    raise ValueError("Expected a JSON result object.")
                return answer
        except HTTPError as exc:
            # Retry only temporary provider responses. Avoid exposing any headers.
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"RU function HTTP {exc.code}") from None
        except (URLError, TimeoutError) as exc:
            if attempt == 2:
                raise RuntimeError(f"RU network request failed: {type(exc).__name__}") from None
        time.sleep(2 * (attempt + 1))
    raise RuntimeError("RU function retries exhausted.")


def summary(report: dict) -> dict:
    results = report.get("results", [])
    works = [r for r in results if r["status"] == "working"]
    stages = Counter((r.get("failure_stage") or "unknown") for r in results if r["status"] != "working")
    codes = Counter((r.get("failure_code") or "unknown") for r in results if r["status"] != "working")
    return {
        "requested": report["requested"], "tested": len(results),
        "working": len(works), "failed": len(results) - len(works),
        "mtproto_tested": sum(r["type"] == "MTProto" for r in results),
        "socks5_tested": sum(r["type"] == "SOCKS5" for r in results),
        "mtproto_working": sum(r["type"] == "MTProto" for r in works),
        "socks5_working": sum(r["type"] == "SOCKS5" for r in works),
        "failure_stages": dict(stages.most_common()),
        "failure_codes": dict(codes.most_common()),
        "fastest_verified": sorted(works, key=lambda r: r.get("latency_ms") or 999999)[:20],
    }


def execute(catalog: dict, url: str, token: str, count: int, report_path: Path) -> dict:
    chosen = choose_sample(catalog, count)
    metadata = {x["id"]: x for x in chosen}
    report = {
        "schema_version": 1,
        "checker": "yandex_cloud_ru", "mode": "bulk_test_only",
        "status": "running", "requested": count,
        "catalog_generated_at": catalog.get("generated_at"),
        "started_at": iso(utc_now()),
        "finished_at": None, "batches_done": 0,
        "results": [],
    }
    working_health = {"schema_version": 1, "cursor": 0, "checks": []}
    save(report_path, report)
    try:
        for offset in range(0, len(chosen), MAX_BATCH):
            batch = chosen[offset: offset + MAX_BATCH]
            request = {"schema_version": 1, "proxies": batch, "rotation_advance": len(batch)}
            answer = call_yandex(url, token, batch)
            # Validate all IDs, types, statuses and timestamps BEFORE recording results.
            working_health = merge(catalog, working_health, request, answer)
            for item in answer["results"]:
                source = metadata[item["proxy_id"]]
                report["results"].append({
                    "proxy_id": item["proxy_id"],
                    "type": source["type"], "server": source["server"], "port": source["port"],
                    "status": item["status"], "latency_ms": item.get("latency_ms"),
                    "elapsed_ms": item.get("elapsed_ms"),
                    "failure_code": item.get("failure_code", "unknown"),
                    "failure_stage": item.get("failure_stage"),
                    "failure_detail": item.get("failure_detail"),
                    "checked_at": item.get("checked_at"),
                })
            report["batches_done"] += 1
            report["summary"] = summary(report)
            save(report_path, report)
            print(f"RU bulk: {len(report['results'])}/{count} checked, "
                  f"{report['summary']['working']} working, {report['summary']['failed']} failed", flush=True)
        report["status"] = "complete"
    except Exception as exc:
        report["status"] = "partial_failure"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)[:160]
        raise
    finally:
        report["finished_at"] = iso(utc_now())
        report["summary"] = summary(report)
        save(report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=ROOT / "data" / "proxies.json")
    parser.add_argument("--output", type=Path, default=ROOT / "ru-bulk-400-report.json")
    parser.add_argument("--count", type=int, default=400)
    args = parser.parse_args()
    url = os.getenv("TGU_RU_FUNCTION_URL", "")
    token = os.getenv("TGU_RU_FUNCTION_API_KEY", "")
    validate_url(url)
    if not token:
        raise SystemExit("TGU_RU_FUNCTION_API_KEY not configured.")
    output = execute(read(args.catalog), url, token, args.count, args.output)
    print("RU bulk sample:", json.dumps(output["summary"], ensure_ascii=False)[:10000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
