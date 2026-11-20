#!/usr/bin/env python3
"""Probe publicly listed audio from the experimental RNMD directory.

A successful HTTP/media probe is *only* a technical result, not a license
verification, confirmation of accessibility in Russia, or permission to host.
No audio files are saved. A bounded Range request reads at most 1024 bytes.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import ipaddress
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_TRACKS = 100
MAX_BYTES = 1024
TIMEOUT = 10
VALID_LICENSES = {"by", "by-sa", "by-nd", "by-nc", "by-nc-sa", "by-nc-nd", "cc0", "pdm"}
ACCEPTED_MIME = {"audio/mpeg", "audio/mp3", "audio/x-mpeg", "audio/ogg",
                 "audio/flac", "audio/x-flac", "application/octet-stream"}


def allowed_host(url: str) -> bool:
    if not isinstance(url, str) or len(url) > 1200:
        return False
    try:
        parts = urlsplit(url)
        hostname = (parts.hostname or "").lower()
        if parts.scheme != "https" or parts.port not in (None, 443):
            return False
        if parts.username or parts.password:
            return False
        if hostname == "jamendo.com" or hostname.endswith(".jamendo.com"):
            return True
        # Reject IP literals (including loopback, link local, private space).
        ipaddress.ip_address(hostname)
        return False
    except (ValueError, TypeError):
        return False


class RestrictedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_host(newurl):
            raise URLError("Blocked cross-provider or non-HTTPS redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def is_mp3(prefix: bytes) -> bool:
    return bool(prefix.startswith(b"ID3") or (
        len(prefix) >= 2 and prefix[0] == 0xFF and prefix[1] & 0xE0 == 0xE0
    ))


def valid_license(track: dict) -> bool:
    kind = str(track.get("license") or "").lower()
    url = str(track.get("license_url") or "")
    if kind not in VALID_LICENSES:
        return False
    return (url.startswith("https://creativecommons.org/licenses/")
            or (kind in {"cc0", "pdm"} and
                url.startswith("https://creativecommons.org/publicdomain/")))


def probe(track: dict, opener=None) -> tuple[dict | None, dict]:
    ref = str(track.get("id") or "")[:150]
    result = {"id": ref, "status": "rejected"}
    url = track.get("audio_url")
    if not allowed_host(url):
        result["reason"] = "untrusted audio hostname"
        return None, result
    if not valid_license(track):
        result["reason"] = "missing/unsupported license attribution"
        return None, result
    if not str(track.get("source_page") or "").startswith("https://www.jamendo.com/track/"):
        result["reason"] = "missing original track page"
        return None, result
    if opener is None:
        opener = build_opener(RestrictedRedirect())
    try:
        request = Request(url, headers={
            "Range": "bytes=0-1023",
            "Accept": "audio/mpeg, audio/*;q=0.9, */*;q=0.5",
            "User-Agent": "RNMDMusicDirectory/0.1 (stream reachability check)",
        })
        with opener.open(request, timeout=TIMEOUT) as response:
            code = response.status
            media_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            final_url = response.geturl()
            if code not in (200, 206):
                raise ValueError("unexpected status")
            if not allowed_host(final_url):
                raise ValueError("unexpected redirect host")
            if media_type not in ACCEPTED_MIME:
                raise ValueError("unexpected content type")
            body = response.read(MAX_BYTES)
            if not is_mp3(body):
                raise ValueError("no MP3 signature in sampled bytes")
            verified = {
                key: track[key] for key in (
                    "id", "provider", "title", "artist", "language", "genre",
                    "license", "license_url", "source_page", "audio_url"
                ) if key in track
            }
            verified["stream_verified"] = True
            verified["rights_reviewed"] = False
            verified["network_ru_verified"] = False
            verified["stream_checked_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            result.update(status="reachable", http_status=code, mime_type=media_type)
            return verified, result
    except HTTPError as exc:
        result.update(status="unavailable", http_status=exc.code)
    except (OSError, URLError, ValueError, TimeoutError) as exc:
        result.update(status="unavailable", reason=str(exc)[:130])
    return None, result


def validate(candidate: dict, max_workers: int = 4) -> dict:
    items = candidate.get("tracks") or []
    if not isinstance(items, list):
        raise ValueError("tracks must be an array")
    items = [x for x in items[:MAX_TRACKS] if isinstance(x, dict)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        probes = list(pool.map(probe, items))
    verified = [found for found, _ in probes if found is not None]
    checks = [check for _, check in probes]
    return {
        "schema_version": 1,
        "directory": "rnmd-music",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "experimental": True,
        "source_count": len({x["provider"] for x in verified}),
        "track_count": len(verified),
        "metadata_candidates": len(items),
        "notes": "Streaming reachable from GitHub Actions runner only. Licenses are unreviewed claims indexed by Openverse. Russian network availability not verified.",
        "verification": {
            "streaming": "http_and_mp3_magic_checked",
            "licensing": "not_independently_verified",
            "russian_network": "not_checked",
        },
        "checks": checks,
        "tracks": verified,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="music-preview.json")
    parser.add_argument("--output", default="music-checked.json")
    parser.add_argument("--require-results", action="store_true")
    args = parser.parse_args()
    index = json.loads(Path(args.input).read_text(encoding="utf-8"))
    checked = validate(index)
    Path(args.output).write_text(
        json.dumps(checked, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Music streams: {checked['track_count']}/{checked['metadata_candidates']} reachable")
    for check in checked["checks"]:
        print(check)
    if args.require_results and not checked["track_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
