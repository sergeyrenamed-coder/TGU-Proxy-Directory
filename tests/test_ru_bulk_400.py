import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.ru_bulk_400 import choose_sample, summary, execute, PINNED_ID


def catalog(count=821):
    items = []
    for i in range(count):
        items.append({
            "id": f"{i:020x}", "type": "SOCKS5" if i % 41 == 0 else "MTProto",
            "server": "1.1.1.1", "port": 443,
            "secret": "a" * 32, "probe_status": "working",
        })
    items[min(400, len(items) - 1)]["id"] = PINNED_ID
    return {"schema_version": 1, "generated_at": "2026-11-18T18:09:24Z", "proxies": items}


class BulkRU400Tests(unittest.TestCase):
    def test_400_unique_and_includes_known_working_proxy(self):
        a = choose_sample(catalog(), 400)
        b = choose_sample(catalog(), 400)
        self.assertEqual(a, b)
        self.assertEqual(400, len(a))
        self.assertEqual(400, len(set(x["id"] for x in a)))
        self.assertEqual(PINNED_ID, a[0]["id"])

    def test_400_rejects_oversize(self):
        with self.assertRaises(ValueError):
            choose_sample(catalog(), 401)

    def test_summarizes_protocols_and_errors(self):
        report = {"requested": 2, "results": [
            {"status": "working", "type": "MTProto", "latency_ms": 100},
            {"status": "failed", "type": "SOCKS5", "failure_stage": "TCP connect",
             "failure_code": "timeout"},
        ]}
        result = summary(report)
        self.assertEqual(1, result["working"])
        self.assertEqual(1, result["failed"])
        self.assertEqual(1, result["socks5_tested"])
        self.assertEqual({"TCP connect": 1}, result["failure_stages"])

    def test_offline_single_batch_end_to_end(self):
        c = catalog(40)
        def fake_call(url, token, batch):
            from scripts.ru_check import iso, utc_now
            return {
                "ok": True, "schema_version": 1, "checker": "yandex_cloud_ru",
                "results": [
                    {"proxy_id": x["id"], "type": x["type"], "status": "working",
                     "latency_ms": 100, "checked_at": iso(utc_now())} for x in batch
                ],
            }
        with TemporaryDirectory() as temp:
            with patch("scripts.ru_bulk_400.call_yandex", side_effect=fake_call) as call:
                output = execute(c, "https://functions.yandexcloud.net/test", "fake", 32, Path(temp) / "report.json")
            self.assertEqual(32, output["summary"]["working"])
            self.assertEqual(1, call.call_count)
            self.assertEqual("complete", output["status"])


if __name__ == "__main__":
    unittest.main()
