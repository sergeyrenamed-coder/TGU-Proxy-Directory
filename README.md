# TGU Proxy Directory

A public, automatically refreshed proxy catalog for the TGU Android application.

## What it does

- Aggregates public Telegram proxy catalogs and a public SOCKS5 feed.
- Keeps only MTProto secrets currently supported by TGU (classic 16-byte secrets and the `dd` padded-intermediate form).
- Excludes FakeTLS/WEB MTProto secrets beginning with `ee` from the TGU-compatible API because the current TGU transport does not support them yet.
- Tests a bounded sample of public SOCKS5 endpoints by negotiating SOCKS5 and requesting a tunnel to a Telegram DC.
- Deduplicates records, preserves the last known records for sources that temporarily fail, and publishes a single JSON catalog.
- Refreshes every 15 minutes through GitHub Actions.

## API

**TGU-compatible catalog (raw JSON):**

`https://raw.githubusercontent.com/sergeyrenamed-coder/TGU-Proxy-Directory/main/data/proxies.json`

**Source status and health:**

`https://raw.githubusercontent.com/sergeyrenamed-coder/TGU-Proxy-Directory/main/data/stats.json`

The API uses records with `type`, `server`, `port`, and protocol-specific fields. That shape is compatible with TGU's recursive Freedom Checker JSON parser.

## Website

The static site is in `docs/`. To publish it once:

1. Open **Settings → Pages** in this repository.
2. Under **Build and deployment**, choose **Deploy from a branch**.
3. Select branch **main** and folder **/docs**, then save.

The site will be available at:

`https://sergeyrenamed-coder.github.io/TGU-Proxy-Directory/`

The page reads the generated JSON files from `docs/data/`, which the collector updates alongside the canonical `data/` files.

## Automatic updates

GitHub Actions runs every 15 minutes, on pushes affecting the collector, and on manual dispatch. The schedule runs from GitHub infrastructure, not from the Android device. A source that is unavailable to the runner is reported as failed; the previous catalog entries from that source are retained and marked stale until the source is reachable again.

## Sources currently configured

- 0xRadikal Free Telegram Proxy — stable and verified tiers, fetched from GitHub Raw.
- tgmtproxy/mtproxy — fetched from GitHub Raw. Its current list is predominantly FakeTLS, so those entries are counted as unsupported and are not published to the TGU-compatible pool.
- Freedom Checker — public snapshot JSON.
- monosans/proxy-list — SOCKS5 candidate feed. A bounded sample is tested before publication.

The collector never uses jsDelivr.

## Trust and privacy

These are public third-party proxy endpoints, not TGU-operated servers. A proxy operator can observe connection metadata and may be able to associate network activity with the client. Do not treat a proxy as trusted merely because it passed an availability check. The checker does not authenticate to a Telegram account or read Telegram messages.

## Local checks

```bash
python -m unittest discover -s tests -v
python scripts/collect.py --socks-limit 100
```
