#!/usr/bin/env python3
"""Safely import a one-time 400-proxy RU-cloud report into the separate RU-health feed.

The input is a GitHub Actions artifact with protocol-level results; it is *not*
an authorization to mark global proxies as RU verified indefinitely.
"""
import argparse
import json
from datetime import timedelta
from pathlib import Path

from scripts.ru_check import candidates, iso, parse_time, read, save, utc_now


def convert(report, catalog, now=None, expected_count=400):
    now = now or utc_now()
    if (report.get("schema_version") != 1 or report.get("checker") != "yandex_cloud_ru"
            or report.get("mode") != "bulk_test_only" or report.get("status") != "complete"
            or report.get("requested") != expected_count):
        raise ValueError("Unexpected RU cloud bulk report")
    rows = report.get("results")
    if (not isinstance(rows, list) or len(rows) != expected_count
            or report.get("catalog_generated_at") != catalog.get("generated_at")):
        raise ValueError("Unexpected batch length or source catalog version")
    finished = parse_time(report.get("finished_at"))
    if finished is None or not timedelta(0) <= now - finished < timedelta(hours=24):
        raise ValueError("Report already stale or from the future")
    by_id = {p["id"]: p for p in candidates(catalog)}
    seen = set()
    checks = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Malformed RU result")
        pid = row.get("proxy_id")
        source = by_id.get(pid)
        when = parse_time(row.get("checked_at"))
        if (pid in seen or source is None or
                row.get("type") != source["type"] or
                row.get("server") != source["server"] or
                row.get("port") != source["port"] or
                row.get("status") not in ("working", "failed") or
                when is None or not timedelta(0) <= now - when < timedelta(hours=24)):
            raise ValueError("Invalid, stale or unknown RU proxy result")
        seen.add(pid)
        status = row["status"]
        delay = row.get("latency_ms")
        if status == "working" and (type(delay) is not int or not 0 <= delay <= 60000):
            raise ValueError("Invalid verified RU latency")
        checks.append({
            "proxy_id": pid,
            "type": source["type"],
            "status": status,
            "latency_ms": delay if status == "working" else None,
            "failure_code": "none" if status == "working" else str(row.get("failure_code") or "unknown")[:48],
            "checked_at": row["checked_at"],
        })
    checks.sort(key=lambda x: x["proxy_id"])
    working = sum(x["status"] == "working" for x in checks)
    if working != 12 or len(checks) - working != 388:
        # These counts are tied to artifact run 44408358945.
        raise ValueError("Run result mismatch; not importing an unknown artifact")
    size = len(by_id)
    return {
        "schema_version": 1,
        "service": "TGU RU Checker",
        "checker": "yandex_cloud_ru",
        "generated_at": iso(finished),
        "status": "active",
        "cursor": 0,
        "ttl_hours": 24,
        "origin": "one_off_yandex_400",
        "source_run_id": 44408358945,
        "totals": {
            "catalog": size,
            "checked": len(checks),
            "working": working,
            "failed": len(checks) - working,
            "coverage_percent": round(100 * len(checks) / size, 1) if size else 0,
        },
        "checks": checks,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--catalog", default="data/proxies.json")
    parser.add_argument("--output", default="data/ru-health.json")
    parser.add_argument("--docs-output", default="docs/data/ru-health.json")
    args = parser.parse_args()
    report = read(args.report)
    result = convert(report, read(args.catalog))
    save(args.output, result)
    save(args.docs_output, result)
    print("RU import: imported", result["totals"]["checked"], "checks,",
          result["totals"]["working"], "working,",
          result["totals"]["coverage_percent"], "% catalog coverage.")


if __name__ == "__main__":
    main()
