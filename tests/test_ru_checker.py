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

    def test_network_diagnostics_has_fixed_targets(self):
        from ru_checker.main import DIAGNOSTIC_TARGETS
        self.assertEqual(["internet_ip", "internet_dns", "telegram_dc"],
                         [target[0] for target in DIAGNOSTIC_TARGETS])
        class FakeSocket:
            def close(self): pass
        with patch("ru_checker.main.connect_public_socket", return_value=FakeSocket()) as connection:
            answer = handler({"httpMethod": "POST", "body": json.dumps({
                "schema_version": 1, "mode": "network_diagnostics",
                "targets": ["127.0.0.1"],
            })}, None)
        body = json.loads(answer["body"])
        self.assertEqual(200, answer["statusCode"])
        self.assertEqual(3, len(body["network"]))
        self.assertEqual(3, connection.call_count)
        self.assertEqual("reachable", body["network"][0]["status"])
        self.assertNotIn("127.0.0.1", str(connection.call_args_list))

    def test_network_diagnostics_reports_connect_failures(self):
        with patch("ru_checker.main.connect_public_socket", side_effect=TimeoutError("timed out")):
            result = handler({"httpMethod": "POST", "body": json.dumps({
                "schema_version": 1, "mode": "network_diagnostics",
            })}, None)
        body = json.loads(result["body"])
        self.assertEqual(200, result["statusCode"])
        self.assertTrue(all(item["status"] == "failed" for item in body["network"]))
        self.assertTrue(all("TimeoutError" in item["error"] for item in body["network"]))

    def test_probe_results_include_precise_failure_reason(self):
        with patch("ru_checker.main.probe_mtproto_candidate",
                   return_value=(False, 3510, "TCP connect:OSError:No reachable public address (TimeoutError)")):
            result = handler({"httpMethod": "POST", "body": json.dumps({
                "schema_version": 1, "proxies": [item(1)],
            })}, None)
        report = json.loads(result["body"])["results"][0]
        self.assertEqual("timeout", report["failure_code"])
        self.assertEqual("TCP connect", report["failure_stage"])
        self.assertIn("TimeoutError", report["failure_detail"])
        self.assertEqual(3510, report["elapsed_ms"])

    def test_rotating_batches(self):
        batch = prepare(catalog(), {"cursor": 3}, 2)
        self.assertEqual([item(4)["id"], item(1)["id"]], [x["id"] for x in batch["proxies"]])

    def test_priority_recheck_does_not_starve_rotation(self):
        dt = datetime(2026, 11, 19, 3, 0, tzinfo=timezone.utc)
        entries = catalog(40)
        previous = {"cursor": 0, "checks": [
            {"proxy_id": item(20)["id"], "status": "working", "checked_at": "2026-11-19T01:00:00Z"},
            {"proxy_id": item(21)["id"], "status": "working", "checked_at": "2026-11-19T01:30:00Z"},
            {"proxy_id": item(22)["id"], "status": "working", "checked_at": "2026-11-19T02:50:00Z"},
        ]}
        batch = prepare(entries, previous, 8, now=dt)
        ids = [x["id"] for x in batch["proxies"]]
        self.assertEqual(8, len(ids))
        self.assertEqual(8, len(set(ids)))
        self.assertIn(item(20)["id"], ids)
        self.assertIn(item(21)["id"], ids)
        self.assertNotIn(item(22)["id"], ids)
        self.assertEqual([item(i)["id"] for i in range(1, 7)], ids[2:])
        self.assertEqual(6, batch["rotation_advance"])

    def test_recent_success_not_rechecked_too_early(self):
        dt = datetime(2026, 11, 19, 3, 0, tzinfo=timezone.utc)
        recent = {"cursor": 0, "checks": [
            {"proxy_id": item(20)["id"], "status": "working", "checked_at": "2026-11-19T02:30:00Z"},
        ]}
        batch = prepare(catalog(40), recent, 8, now=dt)
        self.assertEqual([item(i)["id"] for i in range(1, 9)],
                         [x["id"] for x in batch["proxies"]])
        self.assertEqual(8, batch["rotation_advance"])

    def test_expired_success_not_rechecked_as_priority(self):
        dt = datetime(2026, 11, 19, 3, 0, tzinfo=timezone.utc)
        old = {"checks": [
            {"proxy_id": item(20)["id"], "status": "working", "checked_at": "2026-11-15T03:00:00Z"},
        ]}
        batch = prepare(catalog(40), old, 8, now=dt)
        self.assertEqual(item(1)["id"], batch["proxies"][0]["id"])

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
