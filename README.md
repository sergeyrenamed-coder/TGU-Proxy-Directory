# TGU Proxy Directory

A public, automatically refreshed proxy catalog for the TGU Android application.

## What it does

- Aggregates public Telegram proxy catalogs and a public SOCKS5 feed.
- Keeps only MTProto secrets currently supported by TGU (classic 16-byte secrets and the `dd` padded-intermediate form).
- Excludes FakeTLS/WEB MTProto secrets beginning with `ee` from the TGU-compatible API because the current TGU transport does not support them yet.
- Actively probes every normalized endpoint before publication: classic MTProto uses the obfuscated2 handshake plus `req_pq_multi`/`resPQ`; SOCKS5 must negotiate and open a CONNECT tunnel to a Telegram DC.
- Rejects failed probes and never republishes stale endpoints as working. Proxy health is time-sensitive; TGU repeats checks from the user's own network before selecting a server.
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
2. Under **Build and deployment**, choose **GitHub Actions** as the source.
3. Save. The collector workflow deploys the current `docs/` folder after every successful collection run.

The site will be available at:

`https://sergeyrenamed-coder.github.io/TGU-Proxy-Directory/`

The page reads the generated JSON files from `docs/data/`. The same workflow uploads and deploys this folder after it refreshes the canonical `data/` files, so the site is updated even though the bot commit is made with GitHub's workflow token.

## RNMDSTORE / Netlify mirror

The canonical GitHub catalog can also be mirrored to the RNMDSTORE Netlify API. TGU clients will eventually use this API for catalog delivery and community feedback; GitHub remains the source collector and fallback.

After the RNMDSTORE Netlify Functions are deployed, configure these repository Actions secrets:

- `TGU_NETLIFY_CATALOG_SYNC_URL` — the full URL ending in `/api/tgu/catalog/sync`.
- `TGU_NETLIFY_SYNC_TOKEN` — the same long random token configured as `TGU_CATALOG_SYNC_TOKEN` in Netlify.

The collection workflow sends the newly generated `data/proxies.json` to Netlify after a successful scan. The sync step is non-blocking: when Netlify is unavailable or secrets are not set, the GitHub catalog commit and Pages publication continue normally.

See the RNMDSTORE repository's `docs/TGU_COMMUNITY_NETWORK_SETUP.md` for the complete setup and verification procedure.

## Automatic updates

GitHub Actions runs every 15 minutes, on pushes affecting the collector, and on manual dispatch. The schedule runs from GitHub infrastructure, not from the Android device. A source that is unavailable to the runner is reported as failed. Previously published endpoints from a failed source are not copied into the new working-only catalog.

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
