"""Private Yandex Cloud Function; entrypoint: main.handler."""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from scripts.collect import probe_mtproto_candidate, probe_socks5_candidate, utc_now

ID_RE = re.compile(r"^[a-f0-9]{20}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
SECRET_RE = re.compile(r"^(?:[a-fA-F0-9]{32}|dd[a-fA-F0-9]{32})$")
MAX_BATCH = 32

def respond(status, data):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"}, "body": json.dumps(data)}

def valid(entry):
    if not isinstance(entry, dict):
        return False
    if not isinstance(entry.get("id"), str) or not ID_RE.fullmatch(entry["id"]):
        return False
    if not isinstance(entry.get("server"), str) or not HOST_RE.fullmatch(entry["server"]):
        return False
    if type(entry.get("port")) is not int or not 1 <= entry["port"] <= 65535:
        return False
    if entry.get("type") == "SOCKS5":
        return not entry.get("secret")
    if entry.get("type") == "MTProto":
        return isinstance(entry.get("secret"), str) and bool(SECRET_RE.fullmatch(entry["secret"]))
    return False

def probe(entry):
    if entry["type"] == "MTProto":
        ok, ms, reason = probe_mtproto_candidate(entry["server"], entry["port"], entry["secret"], timeout=3.5)
    else:
        ok, ms, reason = probe_socks5_candidate(entry["server"], entry["port"], timeout=3.5)
    lower = reason.lower()
    failure = "none" if ok else ("timeout" if "timed out" in lower or "timeout" in lower else
              "protocol" if "frame" in lower or "constructor" in lower else
              "connect" if "connect" in lower or "refused" in lower else "unknown")
    return {
        "proxy_id": entry["id"], "type": entry["type"],
        "status": "working" if ok else "failed",
        "latency_ms": ms if ok else None,
        "failure_code": failure, "checked_at": utc_now(),
    }

def handler(event, context):
    if not isinstance(event, dict) or event.get("httpMethod") != "POST":
        return respond(405, {"ok": False, "error": "post_required"})
    raw = event.get("body")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536:
        return respond(413, {"ok": False, "error": "invalid_body_size"})
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        return respond(400, {"ok": False, "error": "invalid_json"})
    if not isinstance(body, dict) or body.get("schema_version") != 1:
        return respond(422, {"ok": False, "error": "invalid_schema"})
    entries = body.get("proxies")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_BATCH:
        return respond(422, {"ok": False, "error": "invalid_batch"})
    if not all(valid(entry) for entry in entries):
        return respond(422, {"ok": False, "error": "invalid_proxy"})
    ids = [entry["id"] for entry in entries]
    if len(ids) != len(set(ids)):
        return respond(422, {"ok": False, "error": "duplicate_id"})
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(probe, entries))
    return respond(200, {
        "ok": True, "schema_version": 1, "checker": "yandex_cloud_ru",
        "checked_at": utc_now(), "results": results,
    })
