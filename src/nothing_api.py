"""Reference read-only HTTP API for the NOTHING v1 contract.

This server intentionally uses only Python's standard library. It is a small,
deployment-neutral reference implementation, not a production internet-facing
service. Verification semantics come from src.nothing_protocol.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from src.nothing_protocol import RelationshipError, resolve_claim_relationships
from src.nothing_verify import (
    EVENT_ID_RE,
    EVIDENCE_ID_RE,
    NOTHING_ID_RE,
    ValidationError,
    load_json,
    validate_evidence,
    validate_verification_event,
    validate_identity,
)

API_VERSION = "1"
PROTOCOL_VERSION = "0.1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080

RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("NOTHING_RATE_WINDOW_SECONDS", "60"))
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("NOTHING_RATE_MAX_REQUESTS", "120"))


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _data_root() -> Path:
    configured = os.getenv("NOTHING_DATA_ROOT")
    return Path(configured).resolve() if configured else _repo_root()


def _examples_dir() -> Path:
    return _data_root() / "examples"


def _procedures_path() -> Path:
    return _data_root() / "procedures" / "registry.json"


def _json_load(path: Path) -> dict[str, Any]:
    return load_json(path)


def _find_record(prefix: str, record_id: str) -> Path | None:
    examples = _examples_dir()
    if not examples.exists():
        return None
    exact = examples / f"{record_id}.json"
    if exact.is_file():
        return exact

    for path in examples.glob(f"{prefix}-*.json"):
        try:
            record = _json_load(path)
        except (OSError, json.JSONDecodeError):
            continue
        key = {
            "NTH": "nothing_id",
            "EVD": "evidence_id",
            "VER": "event_id",
        }[prefix]
        if record.get(key) == record_id:
            return path
    return None


def _load_all(prefix: str, key: str) -> list[dict[str, Any]]:
    examples = _examples_dir()
    if not examples.exists():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(examples.glob(f"{prefix}-*.json")):
        record = _json_load(path)
        if record.get(key):
            records.append(record)
    return records


def _load_identity(nothing_id: str) -> tuple[dict[str, Any], Path]:
    path = _find_record("NTH", nothing_id)
    if path is None:
        raise FileNotFoundError(nothing_id)
    identity = _json_load(path)
    validate_identity(identity)
    return identity, path


def _load_evidence(evidence_id: str) -> tuple[dict[str, Any], Path]:
    path = _find_record("EVD", evidence_id)
    if path is None:
        raise FileNotFoundError(evidence_id)
    record = _json_load(path)
    validate_evidence(record)
    return record, path


def _load_event(event_id: str) -> tuple[dict[str, Any], Path]:
    path = _find_record("VER", event_id)
    if path is None:
        raise FileNotFoundError(event_id)
    record = _json_load(path)
    validate_verification_event(record)
    return record, path


def _load_registry() -> tuple[dict[str, Any], Path]:
    path = _procedures_path()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return _json_load(path), path


def _iso_from_mtime(path: Path) -> str:
    timestamp = path.stat().st_mtime
    from datetime import datetime, timezone
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _http_last_modified(path: Path) -> str:
    return formatdate(path.stat().st_mtime, usegmt=True)


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _etag(payload_bytes: bytes) -> str:
    digest = hashlib.sha256(payload_bytes).hexdigest()
    return f'"{digest}"'


def _error_payload(status: int, code: str, detail: str, instance: str | None = None) -> dict[str, Any]:
    payload = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
        "code": code,
        "detail": detail,
    }
    if instance:
        payload["instance"] = instance
    return payload


def _identity_view(identity: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
    events = {
        event["event_id"]: event
        for event in _load_all("VER", "event_id")
        if event["subject"] == identity["nothing_id"]
    }

    claims = []
    for claim in identity["claims"]:
        report = resolution["claims"][claim["claim_id"]]
        event_id = report["current_event_id"]
        current_event = events.get(event_id) if event_id else None

        current_verification = None
        if current_event:
            current_verification = {
                "event_id": current_event["event_id"],
                "status": current_event["result"]["status"],
                "occurred_at": current_event["occurred_at"],
                "scope": current_event["result"]["scope"],
                "procedure": {
                    "id": current_event["procedure"]["id"],
                    "version": current_event["procedure"]["version"],
                },
                "evidence_ids": list(current_event.get("evidence", [])),
            }

        claims.append(
            {
                "id": claim["claim_id"],
                "statement": claim["statement"],
                "recorded_status": claim["status"],
                "current_verification": current_verification,
            }
        )

    return {
        "id": identity["nothing_id"],
        "version": identity["version"],
        "subject": identity["subject"],
        "claims": claims,
        "revocation": identity.get("revocation"),
    }


def _evidence_view(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["evidence_id"],
        "version": record["version"],
        "type": record["type"],
        "source": record["source"],
        "collected_at": record["collected_at"],
        "integrity": record["integrity"],
        **({"notes": record["notes"]} if "notes" in record else {}),
    }


def _event_view(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["event_id"],
        "version": record["version"],
        "occurred_at": record["occurred_at"],
        "subject": record["subject"],
        "claim_id": record["claim_id"],
        "procedure": record["procedure"],
        "verifier": record.get("verifier"),
        "evidence": list(record.get("evidence", [])),
        "result": record["result"],
        **({"supersedes": record["supersedes"]} if "supersedes" in record else {}),
    }


def _procedure_view(procedure: dict[str, Any]) -> dict[str, Any]:
    return procedure


def _meta(*, demo: bool = True, generated_at: str | None = None) -> dict[str, Any]:
    from datetime import datetime, timezone
    return {
        "api_version": API_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "demo": demo,
    }


def _latest_path(paths: list[Path]) -> Path | None:
    existing = [path for path in paths if path.is_file()]
    return max(existing, key=lambda path: path.stat().st_mtime) if existing else None


def _identity_source_paths(identity_path: Path) -> list[Path]:
    paths = [identity_path, _procedures_path()]
    examples = _examples_dir()
    if examples.exists():
        paths.extend(examples.glob("EVD-*.json"))
        paths.extend(
            path for path in examples.glob("VER-*.json")
            if path.is_file()
        )
    return [path for path in paths if path.is_file()]


def _resolve_identity(identity: dict[str, Any]) -> dict[str, Any]:
    evidence = _load_all("EVD", "evidence_id")
    events = [
        event
        for event in _load_all("VER", "event_id")
        if event.get("subject") == identity["nothing_id"]
    ]
    registry, _ = _load_registry()

    return resolve_claim_relationships(
        identity,
        evidence,
        events,
        registry,
    )


class RateLimiter:
    def __init__(self, window_seconds: int, max_requests: int) -> None:
        self.window_seconds = max(1, window_seconds)
        self.max_requests = max(1, max_requests)
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        current = now if now is not None else time.monotonic()
        with self._lock:
            started, count = self._windows.get(key, (current, 0))
            if current - started >= self.window_seconds:
                started, count = current, 0
            if count >= self.max_requests:
                self._windows[key] = (started, count)
                return False
            self._windows[key] = (started, count + 1)
            return True


RATE_LIMITER = RateLimiter(RATE_LIMIT_WINDOW_SECONDS, RATE_LIMIT_MAX_REQUESTS)


class NothingApiHandler(BaseHTTPRequestHandler):
    server_version = "NOTHING-Reference/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))

    def _client_key(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0]

    def _send(
        self,
        status: int,
        payload: dict[str, Any] | None,
        *,
        content_type: str = "application/json",
        etag: str | None = None,
        last_modified: str | None = None,
        allow_cache: bool = True,
        retry_after: int | None = None,
    ) -> None:
        body = b"" if payload is None else _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-NOTHING-Protocol-Version", PROTOCOL_VERSION)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "public, max-age=60" if allow_cache else "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Access-Control-Allow-Origin", "*")
        if etag:
            self.send_header("ETag", etag)
        if last_modified:
            self.send_header("Last-Modified", last_modified)
        if retry_after is not None:
            self.send_header("Retry-After", str(retry_after))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_problem(self, status: int, code: str, detail: str, instance: str | None = None) -> None:
        self._send(
            status,
            _error_payload(status, code, detail, instance),
            content_type="application/problem+json",
            allow_cache=False,
        )

    def _check_common(self) -> bool:
        if self.command != "GET":
            self._send_problem(405, "METHOD_NOT_ALLOWED", "This reference server is read-only.")
            return False

        if not RATE_LIMITER.allow(self._client_key()):
            self._send_problem(
                429,
                "RATE_LIMITED",
                "Too many requests for this reference server.",
                retry_after=RATE_LIMIT_WINDOW_SECONDS,
            )
            return False

        return True

    def _conditional(self, payload: dict[str, Any], source_paths: list[Path]) -> bool:
        body = _json_bytes(payload)
        etag = _etag(body)
        if_none_match = self.headers.get("If-None-Match")
        if if_none_match and if_none_match.strip() == etag:
            last_modified = None
            if source_paths:
                last_modified = _http_last_modified(max(source_paths, key=lambda p: p.stat().st_mtime))
            self._send(304, None, etag=etag, last_modified=last_modified)
            return True
        return False

    def _serve_json(self, payload: dict[str, Any], source_paths: list[Path]) -> None:
        latest = _latest_path(source_paths)
        generated_at = _iso_from_mtime(latest) if latest else None
        payload = dict(payload)
        if isinstance(payload.get("meta"), dict):
            payload["meta"] = dict(payload["meta"])
            payload["meta"]["generated_at"] = generated_at or payload["meta"].get("generated_at")
        body = _json_bytes(payload)
        etag = _etag(body)
        if self.headers.get("If-None-Match", "").strip() == etag:
            self._send(
                304,
                None,
                etag=etag,
                last_modified=_http_last_modified(latest) if latest else None,
            )
            return
        self._send(
            200,
            payload,
            etag=etag,
            last_modified=_http_last_modified(latest) if latest else None,
        )

    def _method_not_allowed(self) -> None:
        self.send_response(405)
        self.send_header("Allow", "GET, OPTIONS")
        self.send_header("Content-Length", "0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

    def do_POST(self) -> None:
        self._method_not_allowed()

    def do_PUT(self) -> None:
        self._method_not_allowed()

    def do_PATCH(self) -> None:
        self._method_not_allowed()

    def do_DELETE(self) -> None:
        self._method_not_allowed()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Allow", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "If-None-Match, Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if not self._check_common():
            return

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        instance = parsed.path

        try:
            parts = [unquote(p) for p in path.split("/") if p]

            if len(parts) == 3 and parts[:2] == ["v1", "identity"]:
                self._get_identity(parts[2], instance)
                return

            if len(parts) == 3 and parts[:2] == ["v1", "evidence"]:
                self._get_evidence(parts[2], instance)
                return

            if len(parts) == 3 and parts[:2] == ["v1", "verification-events"]:
                self._get_event(parts[2], instance)
                return

            if len(parts) == 4 and parts[:2] == ["v1", "procedures"]:
                self._get_procedure(parts[2], parts[3], instance)
                return

            self._send_problem(404, "NOT_FOUND", "The requested API resource does not exist.", instance)
        except (OSError, json.JSONDecodeError, ValidationError, RelationshipError) as exc:
            self._send_problem(503, "TEMPORARILY_UNAVAILABLE", f"Verification data could not be resolved: {exc}", instance)

    def _get_identity(self, nothing_id: str, instance: str) -> None:
        if not NOTHING_ID_RE.fullmatch(nothing_id):
            self._send_problem(400, "INVALID_ID", "nothing_id must match NTH-XXXXXX.", instance)
            return

        try:
            identity, identity_path = _load_identity(nothing_id)
            resolution = _resolve_identity(identity)
        except FileNotFoundError:
            self._send_problem(404, "NOT_FOUND", "The requested Nothing ID does not exist.", instance)
            return

        payload = {
            "data": _identity_view(identity, resolution),
            "meta": _meta(demo=True),
        }
        self._serve_json(
            payload,
            _identity_source_paths(identity_path),
        )

    def _get_evidence(self, evidence_id: str, instance: str) -> None:
        if not EVIDENCE_ID_RE.fullmatch(evidence_id):
            self._send_problem(400, "INVALID_ID", "evidence_id must match EVD-XXXXXX.", instance)
            return

        try:
            record, path = _load_evidence(evidence_id)
        except FileNotFoundError:
            self._send_problem(404, "NOT_FOUND", "The requested evidence record does not exist.", instance)
            return

        self._serve_json(
            {"data": _evidence_view(record), "meta": _meta(demo=True)},
            [path],
        )

    def _get_event(self, event_id: str, instance: str) -> None:
        if not EVENT_ID_RE.fullmatch(event_id):
            self._send_problem(400, "INVALID_ID", "event_id must match VER-XXXXXX.", instance)
            return

        try:
            record, path = _load_event(event_id)
            # Force relationship resolution so the endpoint cannot serve a
            # structurally valid but semantically unresolvable event.
            identity, _ = _load_identity(record["subject"])
            _resolve_identity(identity)
        except FileNotFoundError:
            self._send_problem(404, "NOT_FOUND", "The requested verification event does not exist.", instance)
            return

        self._serve_json(
            {"data": _event_view(record), "meta": _meta(demo=True)},
            [path],
        )

    def _get_procedure(self, procedure_id: str, version: str, instance: str) -> None:
        if not procedure_id.startswith("NOTHING-") or not version:
            self._send_problem(400, "INVALID_ID", "procedure_id or version is invalid.", instance)
            return

        try:
            registry, path = _load_registry()
        except FileNotFoundError:
            self._send_problem(503, "TEMPORARILY_UNAVAILABLE", "The procedure registry is unavailable.", instance)
            return

        procedure = next(
            (
                item for item in registry["procedures"]
                if item["id"] == procedure_id and item["version"] == version
            ),
            None,
        )
        if procedure is None:
            self._send_problem(404, "NOT_FOUND", "The requested procedure version does not exist.", instance)
            return

        self._serve_json(
            {"data": _procedure_view(procedure), "meta": _meta(demo=True)},
            [path],
        )


def build_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), NothingApiHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the NOTHING reference read-only API server.")
    parser.add_argument("--host", default=os.getenv("NOTHING_API_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("NOTHING_API_PORT", str(DEFAULT_PORT))))
    args = parser.parse_args()

    server = build_server(args.host, args.port)
    print(f"NOTHING Reference API listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping NOTHING Reference API.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
