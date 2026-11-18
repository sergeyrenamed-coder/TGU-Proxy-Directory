import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from ru_checker.main import handler, valid
from scripts.ru_check import prepare, merge

def item(i, proto="MTProto"):
    r = {"id": f"{i:020x}", "type": proto, "server": "1.1.1.1", "port": 443}
    if proto == "MTProto":
        r["secret"] = "a" * 32
    return r

def catalog(n=4):
    return {"schema_version": 1, "proxies": [{**item(i), "probe_status": "working"} for i in range(1, n+1)]}

class RUCheckerTest(unittest.TestCase):
    def test_validates_payload(self):
        self.assertTrue(valid(item(1)))
        self.assertFalse(valid({**item(1), "port": 0}))
        self.assertFalse(valid({**item(1), "secret": "ee" + "a"*32}))
        self.assertFalse(valid({**item(1), "type": "HTTP"}))

    def test_rejects_bad_requests(self):
        for body in ("not json", json.dumps({"schema_version": 1, "proxies": []}),
                     json.dumps({"schema_version": 1, "proxies": [{"invalid": True}]})):
            self.assertNotEqual(200, handler({"httpMethod": "POST", "body": body}, None)["statusCode"])

    def test_protocol_test_used(self):
        with patch("ru_checker.main.probe_mtproto_candidate", return_value=(True, 74, "ok")) as check:
            answer = handler({"httpMethod": "POST", "body": json.dumps({
                "schema_version": 1, "proxies": [item(1)],
            })}, None)
        self.assertEqual(200, answer["statusCode"])
        self.assertEqual("working", json.loads(answer["body"])["results"][0]["status"])
        check.assert_called_once()

    def test_rotating_batches(self):
        batch = prepare(catalog(), {"cursor": 3}, 2)
        self.assertEqual([item(4)["id"], item(1)["id"]], [x["id"] for x in batch["proxies"]])

    def test_merging_and_expiration(self):
        dt = datetime(2026, 11, 19, tzinfo=timezone.utc)
        batch = prepare(catalog(), {"cursor": 0}, 2)
        answer = {"schema_version": 1, "ok": True, "checker": "yandex_cloud_ru", "results": [
            {"proxy_id": item(1)["id"], "type": "MTProto", "status": "working", "latency_ms": 70,
             "checked_at": "2026-11-19T00:00:00Z"},
            {"proxy_id": item(2)["id"], "type": "MTProto", "status": "failed", "latency_ms": None,
             "checked_at": "2026-11-19T00:00:00Z"},
        ]}
        old = {"cursor": 0, "checks": [
            {"proxy_id": item(3)["id"], "type": "MTProto", "status": "working",
             "checked_at": "2026-11-10T00:00:00Z"},
        ]}
        health = merge(catalog(), old, batch, answer, now=dt)
        self.assertEqual(1, health["totals"]["working"])
        self.assertEqual(2, health["totals"]["checked"])
        self.assertEqual(2, health["cursor"])
        self.assertEqual(50.0, health["totals"]["coverage_percent"])

    def test_rejects_forged_response(self):
        dt = datetime(2026, 11, 19, tzinfo=timezone.utc)
        batch = prepare(catalog(), {}, 1)
        answer = {"schema_version": 1, "ok": True, "checker": "yandex_cloud_ru", "results": [
            {"proxy_id": item(999)["id"], "type": "MTProto", "status": "working", "latency_ms": 1,
             "checked_at": "2026-11-19T00:00:00Z"},
        ]}
        with self.assertRaises(ValueError):
            merge(catalog(), {}, batch, answer, now=dt)

if __name__ == "__main__":
    unittest.main()
