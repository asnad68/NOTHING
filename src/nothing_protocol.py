"""Relationship and procedure semantics for the NOTHING v0.1 protocol.

This module intentionally separates:
1. structural validation of individual records, and
2. semantic resolution of relationships between Identity, Claim, Evidence,
   Verification Event and Procedure Registry records.

It does not determine whether external evidence is truthful. It only checks
whether the supplied records form a coherent, auditable protocol bundle.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
import re

from src.nothing_verify import (
    EVIDENCE_TYPES,
    EVENT_STATUSES,
    ValidationError,
    load_json,
    validate_evidence,
    validate_identity,
    validate_verification_event,
)

PROCEDURE_STATUSES = {"DRAFT", "ACTIVE", "DEPRECATED", "RETIRED"}
PROCEDURE_ID_RE = re.compile(r"^NOTHING-[A-Z0-9-]+$")
STEP_ID_RE = re.compile(r"^STEP-[0-9]{2}$")
METHOD_CLASSES = {
    "source_check",
    "domain_control",
    "authorization_check",
    "identity_attribute_check",
    "other",
}


class RelationshipError(ValueError):
    """Raised when a NOTHING protocol bundle is semantically inconsistent."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RelationshipError(message)


def _reject_extra_keys(record: Mapping[str, Any], allowed: set[str], field: str) -> None:
    extras = set(record.keys()) - allowed
    _require(not extras, f"{field} contains unsupported fields: {sorted(extras)}")


def _parse_datetime(value: str, field: str) -> datetime:
    _require(isinstance(value, str) and bool(value.strip()), f"{field} must be a non-empty string")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RelationshipError(f"{field} must be a valid ISO-8601 date-time") from exc


def _index_unique(records: list[Mapping[str, Any]], field: str, label: str) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for record in records:
        key = record.get(field)
        _require(isinstance(key, str) and key, f"{label} requires a non-empty {field}")
        _require(key not in index, f"duplicate {label} {field}: {key}")
        index[key] = record
    return index


def validate_procedure(procedure: Mapping[str, Any]) -> None:
    """Validate one procedure record against the procedure contract."""
    _require(isinstance(procedure, Mapping), "procedure must be an object")
    _reject_extra_keys(
        procedure,
        {"id", "version", "title", "description", "status", "method_class", "allowed_results",
         "requires_evidence", "evidence_types", "steps", "published_at", "deprecated_at",
         "retired_at", "notes"},
        "procedure",
    )
    _require(
        isinstance(procedure.get("id"), str)
        and bool(procedure["id"].strip())
        and PROCEDURE_ID_RE.fullmatch(procedure["id"]) is not None,
        "procedure.id must be a non-empty string",
    )
    _require(
        isinstance(procedure.get("version"), str) and procedure["version"].strip(),
        "procedure.version must be a non-empty string",
    )
    _require(
        isinstance(procedure.get("title"), str) and procedure["title"].strip(),
        "procedure.title must be a non-empty string",
    )
    _require(procedure.get("status") in PROCEDURE_STATUSES, "procedure.status is not supported")
    _require(procedure.get("method_class") in METHOD_CLASSES, "procedure.method_class is not supported")

    allowed_results = procedure.get("allowed_results")
    _require(
        isinstance(allowed_results, list) and len(allowed_results) > 0,
        "procedure.allowed_results must be a non-empty array",
    )
    _require(
        len(allowed_results) == len(set(allowed_results)),
        "procedure.allowed_results must be unique",
    )
    for status in allowed_results:
        _require(isinstance(status, str) and status in EVENT_STATUSES, "procedure.allowed_results contains an unsupported result")

    _require(
        isinstance(procedure.get("requires_evidence"), bool),
        "procedure.requires_evidence must be a boolean",
    )

    if "description" in procedure:
        _require(
            isinstance(procedure["description"], str) and bool(procedure["description"].strip()),
            "procedure.description must be a non-empty string",
        )

    if "notes" in procedure:
        _require(
            isinstance(procedure["notes"], str) and bool(procedure["notes"].strip()),
            "procedure.notes must be a non-empty string",
        )

    if "evidence_types" in procedure:
        evidence_types = procedure["evidence_types"]
        _require(isinstance(evidence_types, list), "procedure.evidence_types must be an array")
        _require(all(isinstance(item, str) for item in evidence_types), "procedure.evidence_types must contain strings")
        _require(len(evidence_types) == len(set(evidence_types)), "procedure.evidence_types must be unique")
        for evidence_type in evidence_types:
            _require(evidence_type in EVIDENCE_TYPES, "procedure.evidence_types contains an unsupported type")

    steps = procedure.get("steps")
    _require(isinstance(steps, list) and len(steps) > 0, "procedure.steps must be a non-empty array")
    step_ids: set[str] = set()
    for step in steps:
        _require(isinstance(step, Mapping), "each procedure step must be an object")
        _reject_extra_keys(step, {"step_id", "description"}, "procedure.step")
        step_id = step.get("step_id")
        _require(
            isinstance(step_id, str) and STEP_ID_RE.fullmatch(step_id) is not None,
            "procedure.step_id must match STEP-XX",
        )
        _require(step_id not in step_ids, f"duplicate procedure step_id: {step_id}")
        step_ids.add(step_id)
        _require(
            isinstance(step.get("description"), str) and step["description"].strip(),
            "procedure step description must be non-empty",
        )

    published_at = procedure.get("published_at")
    deprecated_at = procedure.get("deprecated_at")
    retired_at = procedure.get("retired_at")

    if published_at is not None:
        _parse_datetime(published_at, "procedure.published_at")
    if procedure["status"] == "DEPRECATED":
        _require(deprecated_at is not None, "DEPRECATED procedures require deprecated_at")
    if procedure["status"] == "RETIRED":
        _require(retired_at is not None, "RETIRED procedures require retired_at")
    if deprecated_at is not None:
        _parse_datetime(deprecated_at, "procedure.deprecated_at")
    if retired_at is not None:
        _parse_datetime(retired_at, "procedure.retired_at")

    if published_at and deprecated_at:
        _require(
            _parse_datetime(published_at, "procedure.published_at")
            <= _parse_datetime(deprecated_at, "procedure.deprecated_at"),
            "procedure.deprecated_at must not precede published_at",
        )
    if published_at and retired_at:
        _require(
            _parse_datetime(published_at, "procedure.published_at")
            <= _parse_datetime(retired_at, "procedure.retired_at"),
            "procedure.retired_at must not precede published_at",
        )
    if deprecated_at and retired_at:
        _require(
            _parse_datetime(deprecated_at, "procedure.deprecated_at")
            <= _parse_datetime(retired_at, "procedure.retired_at"),
            "procedure.retired_at must not precede deprecated_at",
        )


def validate_procedure_registry(registry: Mapping[str, Any]) -> None:
    """Validate the registry and enforce unique (procedure id, version) pairs."""
    _require(isinstance(registry, Mapping), "procedure registry must be an object")
    _reject_extra_keys(registry, {"registry_id", "version", "updated_at", "procedures"}, "procedure registry")
    _require(
        registry.get("registry_id") == "NOTHING-PROCEDURE-REGISTRY",
        "invalid procedure registry id",
    )
    _require(registry.get("version") == "0.1", "procedure registry version must be 0.1")
    if "updated_at" in registry:
        _parse_datetime(registry["updated_at"], "procedure registry updated_at")

    procedures = registry.get("procedures")
    _require(isinstance(procedures, list) and procedures, "procedure registry must contain procedures")

    seen: set[tuple[str, str]] = set()
    for procedure in procedures:
        validate_procedure(procedure)
        key = (procedure["id"], procedure["version"])
        _require(key not in seen, f"duplicate procedure registration: {key[0]}@{key[1]}")
        seen.add(key)


def resolve_procedure(
    registry: Mapping[str, Any],
    procedure_id: str,
    procedure_version: str,
) -> Mapping[str, Any]:
    """Return the exact registered procedure requested by a verification event."""
    validate_procedure_registry(registry)
    for procedure in registry["procedures"]:
        if procedure["id"] == procedure_id and procedure["version"] == procedure_version:
            return procedure
    raise RelationshipError(
        f"verification procedure not registered: {procedure_id}@{procedure_version}"
    )


def _validate_event_procedure_window(
    event: Mapping[str, Any],
    procedure: Mapping[str, Any],
) -> None:
    occurred_at = _parse_datetime(event["occurred_at"], "verification event occurred_at")
    published_at = procedure.get("published_at")
    deprecated_at = procedure.get("deprecated_at")
    retired_at = procedure.get("retired_at")

    if published_at:
        _require(
            occurred_at >= _parse_datetime(published_at, "procedure.published_at"),
            "verification event predates procedure publication",
        )
    if procedure["status"] == "DEPRECATED" and deprecated_at:
        _require(
            occurred_at <= _parse_datetime(deprecated_at, "procedure.deprecated_at"),
            "verification event uses a deprecated procedure after its deprecation time",
        )
    if procedure["status"] == "RETIRED" and retired_at:
        _require(
            occurred_at <= _parse_datetime(retired_at, "procedure.retired_at"),
            "verification event uses a retired procedure after its retirement time",
        )


def _ensure_no_supersession_cycle(
    events_by_id: Mapping[str, Mapping[str, Any]],
) -> None:
    def visit(event_id: str, stack: set[str]) -> None:
        if event_id in stack:
            raise RelationshipError(f"verification event supersession cycle detected at {event_id}")
        event = events_by_id[event_id]
        parent_id = event.get("supersedes")
        if parent_id:
            _require(parent_id in events_by_id, f"superseded event not found: {parent_id}")
            visit(parent_id, stack | {event_id})

    for event_id in events_by_id:
        visit(event_id, set())


def resolve_claim_relationships(
    identity: Mapping[str, Any],
    evidence_records: list[Mapping[str, Any]],
    verification_events: list[Mapping[str, Any]],
    procedure_registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve one identity bundle into deterministic claim relationships.

    The resolver is strict about referential integrity and chronology. It does
    not mutate the identity record and does not decide whether evidence is
    truthful.
    """
    validate_identity(identity)
    validate_procedure_registry(procedure_registry)

    claims = {claim["claim_id"]: claim for claim in identity["claims"]}
    evidence_by_id = _index_unique(evidence_records, "evidence_id", "evidence")
    events_by_id = _index_unique(verification_events, "event_id", "verification event")

    for evidence in evidence_records:
        validate_evidence(evidence)

    for event in verification_events:
        validate_verification_event(event)
        _require(
            event["subject"] == identity["nothing_id"],
            f"verification event {event['event_id']} belongs to {event['subject']}, not {identity['nothing_id']}",
        )
        _require(
            event["claim_id"] in claims,
            f"verification event {event['event_id']} references unknown claim {event['claim_id']}",
        )

        procedure = resolve_procedure(
            procedure_registry,
            event["procedure"]["id"],
            event["procedure"]["version"],
        )
        _require(
            event["result"]["status"] in procedure["allowed_results"],
            (
                f"procedure {procedure['id']}@{procedure['version']} "
                f"does not allow result {event['result']['status']}"
            ),
        )
        _validate_event_procedure_window(event, procedure)

        referenced_evidence = event.get("evidence", [])
        if procedure["requires_evidence"]:
            _require(
                len(referenced_evidence) > 0,
                f"event {event['event_id']} requires at least one evidence reference",
            )
        for evidence_id in referenced_evidence:
            _require(
                evidence_id in evidence_by_id,
                f"verification event {event['event_id']} references unknown evidence {evidence_id}",
            )
            if procedure.get("evidence_types"):
                _require(
                    evidence_by_id[evidence_id]["type"] in procedure["evidence_types"],
                    (
                        f"evidence {evidence_id} type {evidence_by_id[evidence_id]['type']} "
                        f"is not allowed by procedure {procedure['id']}@{procedure['version']}"
                    ),
                )

        if event.get("supersedes"):
            prior_id = event["supersedes"]
            _require(prior_id in events_by_id, f"superseded event not found: {prior_id}")
            prior = events_by_id[prior_id]
            _require(
                prior["subject"] == event["subject"] and prior["claim_id"] == event["claim_id"],
                f"event {event['event_id']} cannot supersede an event for a different subject or claim",
            )
            _require(
                _parse_datetime(event["occurred_at"], "verification event occurred_at")
                >= _parse_datetime(prior["occurred_at"], "superseded event occurred_at"),
                f"event {event['event_id']} must not precede superseded event {prior_id}",
            )

    _ensure_no_supersession_cycle(events_by_id)

    successors: dict[str, list[str]] = {event_id: [] for event_id in events_by_id}
    for event in verification_events:
        prior_id = event.get("supersedes")
        if prior_id:
            successors[prior_id].append(event["event_id"])

    for prior_id, children in successors.items():
        _require(
            len(children) <= 1,
            (
                f"parallel supersession is not supported in v0.1: "
                f"{prior_id} has successors {sorted(children)}"
            ),
        )

    claims_report: dict[str, Any] = {}
    referenced_evidence_ids: set[str] = set()

    for claim_id, claim in sorted(claims.items()):
        claim_events = [
            event
            for event in verification_events
            if event["claim_id"] == claim_id
        ]
        claim_events.sort(
            key=lambda event: (
                _parse_datetime(event["occurred_at"], "verification event occurred_at"),
                event["event_id"],
            )
        )

        superseded_ids = {
            event["supersedes"]
            for event in claim_events
            if event.get("supersedes")
        }
        current_events = [
            event for event in claim_events
            if event["event_id"] not in superseded_ids
        ]
        _require(
            len(current_events) <= 1,
            (
                f"claim {claim_id} has multiple current verification events; "
                "v0.1 requires a single supersession chain"
            ),
        )

        evidence_ids: list[str] = []
        for event in claim_events:
            for evidence_id in event.get("evidence", []):
                referenced_evidence_ids.add(evidence_id)
                if evidence_id not in evidence_ids:
                    evidence_ids.append(evidence_id)

        current_event = current_events[0] if current_events else None
        current_status = current_event["result"]["status"] if current_event else None

        claims_report[claim_id] = {
            "statement": claim["statement"],
            "identity_status": claim["status"],
            "evidence_ids": sorted(evidence_ids),
            "verification_event_ids": [event["event_id"] for event in claim_events],
            "current_event_id": current_event["event_id"] if current_event else None,
            "current_status": current_status,
            "status_consistency": (
                "NO_EVENT"
                if current_event is None
                else "CONSISTENT"
                if current_status == claim["status"]
                else "STATUS_MISMATCH"
            ),
        }

    return {
        "nothing_id": identity["nothing_id"],
        "claims": claims_report,
        "unreferenced_evidence": sorted(set(evidence_by_id) - referenced_evidence_ids),
        "verification_event_count": len(verification_events),
        "evidence_count": len(evidence_records),
        "resolution_version": "0.1",
    }


def resolve_files(
    identity_path: str | Path,
    evidence_paths: list[str | Path],
    event_paths: list[str | Path],
    procedure_registry_path: str | Path,
) -> dict[str, Any]:
    """Convenience loader for a filesystem-backed bundle."""
    identity = load_json(identity_path)
    evidence_records = [load_json(path) for path in evidence_paths]
    events = [load_json(path) for path in event_paths]
    registry = load_json(procedure_registry_path)
    return resolve_claim_relationships(identity, evidence_records, events, registry)
