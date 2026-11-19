#!/usr/bin/env python3
"""Experimental RNMD Music Directory metadata collector.

Uses documented Openverse API, and optional official Jamendo API when
JAMENDO_CLIENT_ID is supplied. Never downloads or rehosts audio.
PromoDJ intentionally has no adapter until an approved API is available.

The result is *candidates*, not a rights-verified/publicly playable catalog.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

USER_AGENT = "RNMDMusicDirectoryPrototype/0.1 (+https://github.com/sergeyrenamed-coder/TGU-Proxy-Directory)"
OPENVERSE_API = "https://api.openverse.org/v1/audio/"
JAMENDO_API = "https://api.jamendo.com/v3.0/tracks/"
ALLOWED_AUDIO_FORMATS = {"mp3", "mp31", "mp32", "ogg", "flac", "wav", "m4a", "opus"}
MAX_RESULTS = 30


def safe_https(value: Any) -> str | None:
    """Reject http:, javascript:, file:, credentials and invalid URLs."""
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    return value


def request_json(base: str, parameters: dict[str, Any], timeout: int = 12) -> dict[str, Any]:
    url = base + "?" + urlencode(parameters)
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        payload = response.read(2_000_001)
        if len(payload) > 2_000_000:
            raise RuntimeError("JSON payload too large")
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise RuntimeError("Unexpected JSON response")
        return result


def normalize_openverse(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Metadata-only. Openverse does NOT verify that licenses are valid."""
    track_id = str(entry.get("id") or "").strip()
    title = str(entry.get("title") or "").strip()
    creator = str(entry.get("creator") or "").strip()
    license_name = str(entry.get("license") or "").strip().lower()
    audio_url = safe_https(entry.get("url"))
    source_page = safe_https(entry.get("foreign_landing_url"))
    license_url = safe_https(entry.get("license_url"))
    category = str(entry.get("category") or "").lower()
    file_type = str(entry.get("filetype") or "").lower().strip(" .")
    if not (track_id and title and creator and source_page and audio_url and license_name):
        return None
    if category != "music" or file_type not in ALLOWED_AUDIO_FORMATS:
        return None
    # A license claim is required; verification is still an independent step.
    if not (license_name.startswith("by") or license_name in {"cc0", "pdm"}):
        return None
    return {
        "id": f"openverse:{track_id}",
        "provider": "openverse",
        "title": title[:200],
        "artist": creator[:200],
        "language": "unknown",  # Language is not inferred from query or alphabet.
        "genre": ", ".join(
            str(genre) for genre in (entry.get("genres") or [])[:4]
        )[:130],
        "license": license_name,
        "license_url": license_url,
        "source_page": source_page,
        "audio_url": audio_url,
        "filetype": file_type,
        "stream_verified": False,
        "rights_reviewed": False,
        "network_ru_verified": False,
    }


def normalize_jamendo(entry: dict[str, Any]) -> dict[str, Any] | None:
    track_id = str(entry.get("id") or "").strip()
    title = str(entry.get("name") or "").strip()
    artist = str(entry.get("artist_name") or "").strip()
    audio_url = safe_https(entry.get("audio"))
    source_page = safe_https(entry.get("shareurl") or entry.get("shorturl"))
    if not (track_id.isdigit() and title and artist and audio_url and source_page):
        return None
    licenses = entry.get("licenses") or {}
    if not isinstance(licenses, dict):
        licenses = {}
    license_url = safe_https(
        licenses.get("ccurl") or entry.get("license_ccurl")
    )
    return {
        "id": f"jamendo:{track_id}",
        "provider": "jamendo",
        "title": title[:200],
        "artist": artist[:200],
        "language": "unknown",
        "genre": "",
        "license": "Jamendo / see source",
        "license_url": license_url,
        "source_page": source_page,
        "audio_url": audio_url,
        "filetype": "mp3",
        "stream_verified": False,
        "rights_reviewed": False,
        "network_ru_verified": False,
    }


def collect_openverse(query: str, limit: int = 15) -> list[dict[str, Any]]:
    payload = request_json(
        OPENVERSE_API, {"q": query, "page_size": min(limit, MAX_RESULTS), "category": "music"}
    )
    items = payload.get("results") or []
    if not isinstance(items, list):
        return []
    normalized = [track for x in items if isinstance(x, dict)
                  if (track := normalize_openverse(x)) is not None]
    # Diagnose source schema without printing private or audio URLs.
    sample = [{
        "category": x.get("category"), "filetype": x.get("filetype"),
        "license": x.get("license"),
        "source_scheme": urlsplit(str(x.get("foreign_landing_url") or "")).scheme,
        "audio_scheme": urlsplit(str(x.get("url") or "")).scheme
    } for x in items[:3] if isinstance(x, dict)]
    print(f"Openverse [{query}]: raw={len(items)}, accepted={len(normalized)}, "
          f"result_count={payload.get('result_count')}, examples={sample}")
    return normalized


def collect_jamendo(query: str, client_id: str, limit: int = 15) -> list[dict[str, Any]]:
    payload = request_json(
        JAMENDO_API, {
            "client_id": client_id, "format": "json", "limit": min(limit, MAX_RESULTS),
            "search": query, "include": "licenses", "type": "single albumtrack",
            "audioformat": "mp32",
        }
    )
    header = payload.get("headers") or {}
    if header.get("status") != "success":
        raise RuntimeError("Jamendo API returned unsuccessful response")
    items = payload.get("results") or []
    if not isinstance(items, list):
        return []
    return [track for x in items if isinstance(x, dict)
            if (track := normalize_jamendo(x)) is not None]


def build_candidate_index(
    queries: list[str],
    *,
    jamendo_client_id: str | None = None,
    openverse: bool = True,
) -> dict[str, Any]:
    tracks: dict[str, dict[str, Any]] = {}
    statuses = []
    for raw_query in queries:
        query = raw_query.strip()[:100]
        if not query:
            continue
        for provider, enabled, collector in [
            ("openverse", openverse, lambda: collect_openverse(query)),
            ("jamendo", bool(jamendo_client_id),
             lambda: collect_jamendo(query, str(jamendo_client_id))),
        ]:
            if not enabled:
                statuses.append({"provider": provider, "query": query,
                                 "status": "disabled"})
                continue
            try:
                result = collector()
                for item in result:
                    tracks[item["id"]] = item
                statuses.append({"provider": provider, "query": query,
                                 "status": "ok", "candidates": len(result)})
            except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError) as exc:
                statuses.append({"provider": provider, "query": query,
                                 "status": "error", "reason": str(exc)[:200]})
    return {
        "schema_version": 1,
        "directory": "rnmd-music",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "experimental": True,
        "source_count": len({t["provider"] for t in tracks.values()}),
        "track_count": len(tracks),
        "verification": {
            "streaming": "unverified",
            "licensing": "unverified",
            "russian_network": "unverified",
            "note": "For testing only; do not publish candidates as playable tracks until checks."
        },
        "provider_checks": statuses,
        "tracks": sorted(tracks.values(), key=lambda x: (x["provider"], x["id"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="RNMD Music metadata experiment")
    parser.add_argument("--query", action="append", dest="queries", default=None,
                        help="Search term; repeat for RU and EN queries")
    parser.add_argument("--output", default="music-preview.json",
                        help="Output JSON in local path, not the public catalog")
    parser.add_argument("--no-openverse", action="store_true",
                        help="Disable Openverse")
    parser.add_argument("--require-results", action="store_true",
                        help="Exit 2 if no candidate tracks were retrieved")
    args = parser.parse_args()
    queries = args.queries or ["русская музыка", "indie pop"]
    data = build_candidate_index(
        queries,
        jamendo_client_id=os.getenv("JAMENDO_CLIENT_ID"),
        openverse=not args.no_openverse
    )
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    print("RNMD candidates:", data["track_count"])
    for item in data["provider_checks"]:
        print(item)
    print("Saved to:", dest)
    if args.require_results and not data["track_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
