import unittest
from datetime import timedelta

from scripts.import_ru_bulk import convert
from scripts.ru_check import iso, utc_now


def sample():
    now = utc_now()
    catalog = {"schema_version": 1, "generated_at": iso(now),
               "proxies": [{"id": f"{n:020x}", "server": "example.com",
                            "port": 443, "type": "MTProto",
                            "secret": "a" * 32, "probe_status": "working"} for n in range(400)]}
    rows = []
    for n in range(400):
        ok = n < 12
        rows.append({
            "proxy_id": f"{n:020x}", "server": "example.com", "port": 443,
            "type": "MTProto", "status": "working" if ok else "failed",
            "latency_ms": 200 if ok else None,
            "failure_code": "none" if ok else "timeout",
            "checked_at": iso(now),
        })
    report = {"schema_version": 1, "checker": "yandex_cloud_ru", "mode": "bulk_test_only",
              "status": "complete", "requested": 400, "catalog_generated_at": iso(now),
              "finished_at": iso(now), "results": rows}
    return report, catalog


class ImportBulkRU(unittest.TestCase):
    def test_400_import_contains_real_statuses(self):
        report, catalog = sample()
        result = convert(report, catalog)
        self.assertEqual(400, result["totals"]["checked"])
        self.assertEqual(12, result["totals"]["working"])
        self.assertEqual(388, result["totals"]["failed"])
        self.assertEqual(100.0, result["totals"]["coverage_percent"])
        self.assertEqual("active", result["status"])
        self.assertEqual(400, len(result["checks"]))

    def test_rejects_stale_report(self):
        report, catalog = sample()
        report["finished_at"] = iso(utc_now() - timedelta(hours=25))
        with self.assertRaises(ValueError):
            convert(report, catalog)

    def test_rejects_forged_working(self):
        report, catalog = sample()
        report["results"][13]["status"] = "working"
        report["results"][13]["latency_ms"] = 200
        with self.assertRaises(ValueError):
            convert(report, catalog)

    def test_rejects_changed_proxy_target(self):
        report, catalog = sample()
        report["results"][0]["port"] = 22
        with self.assertRaises(ValueError):
            convert(report, catalog)

    def test_rejects_duplicate_ids(self):
        report, catalog = sample()
        report["results"][0] = report["results"][1].copy()
        with self.assertRaises(ValueError):
            convert(report, catalog)


if __name__ == "__main__":
    unittest.main()
