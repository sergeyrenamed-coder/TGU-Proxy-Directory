import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collect


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.catalog = collect.Catalog()
        for source in collect.SOURCES:
            self.catalog.start_source(source)

    def test_classic_mtproto_is_supported(self):
        self.assertTrue(collect.mtproto_supported("0123456789abcdef0123456789abcdef"))

    def test_padded_intermediate_mtproto_is_supported(self):
        self.assertTrue(collect.mtproto_supported("dd0123456789abcdef0123456789abcdef"))

    def test_faketls_is_excluded(self):
        self.assertFalse(collect.mtproto_supported("ee" + "1" * 64))

    def test_invalid_secret_is_excluded(self):
        self.assertFalse(collect.mtproto_supported("not-a-secret"))

    def test_mtproto_is_normalized_for_tgu_parser(self):
        ok = self.catalog.add_mtproto({
            "server": "proxy.example.org", "port": 443,
            "secret": "dd0123456789abcdef0123456789abcdef", "type": "secure",
            "country": "DE", "rtt_ms": 120, "uptime": 0.98,
        }, "radikal-stable", "stable")
        self.assertTrue(ok)
        item = next(iter(self.catalog.records.values()))
        self.assertEqual(item["type"], "MTProto")
        self.assertEqual(item["server"], "proxy.example.org")
        self.assertEqual(item["port"], 443)
        self.assertTrue(item["enabled"])

    def test_dedup_merges_source_ids(self):
        record = {"server": "1.2.3.4", "port": 443, "secret": "0123456789abcdef0123456789abcdef"}
        self.catalog.add_mtproto(record, "radikal-stable", "stable")
        self.catalog.add_mtproto(record, "radikal-verified", "verified")
        self.assertEqual(len(self.catalog.records), 1)
        self.assertEqual(set(next(iter(self.catalog.records.values()))["sources"]), {"radikal-stable", "radikal-verified"})

    def test_telegram_link_parser(self):
        parsed = collect.parse_link("https://t.me/proxy?server=1.2.3.4&port=443&secret=0123456789abcdef0123456789abcdef")
        self.assertEqual(parsed["server"], "1.2.3.4")
        self.assertEqual(parsed["port"], "443")
        self.assertTrue(collect.mtproto_supported(parsed["secret"]))

    def test_disabled_or_invalid_hosts_are_rejected(self):
        self.assertIsNone(collect.valid_host("bad host"))
        self.assertIsNone(collect.valid_port(70000))


if __name__ == "__main__":
    unittest.main()
