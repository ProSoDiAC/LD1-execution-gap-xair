from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from fastapi import FastAPI, HTTPException
from typing import Literal

from pydantic import BaseModel, Field

from xair import __version__
from xair.adapters.runtime_state import (read_snapshot, read_snapshot_doc, read_snapshot_full, read_snapshot_timed,
                                         runtime, store, update_context_store_timed)
from xair.core.lifecycle import InvalidTransition
from xair.core.models import ActionIntent, DecisionOutcome, EffectStatus, IntentRecord, IntentState
from xair.core.versioning import _MISSING, get_path

app = FastAPI(title="XAIR Runtime", version=__version__)

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "action-intent-v1.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text())
_VALIDATOR = jsonschema.Draft202012Validator(_SCHEMA, format_checker=jsonschema.FormatChecker())


def _schema_error(body) -> str | None:
    """Return the first AIS v1 schema violation, or None if body is valid."""
    if not isinstance(body, dict):
        return "body_not_an_object"
    errors = sorted(_VALIDATOR.iter_errors(body), key=lambda e: list(e.path))
    if not errors:
        return None
    e = errors[0]
    loc = ".".join(str(p) for p in e.path) or "<root>"
    return f"{loc}: {e.message}"


def _rejection(intent_id, reason: str, version: int, trusted: bool) -> dict:
    return {
        "id": intent_id,
        "state": IntentState.REVOKED.value,
        "outcome": DecisionOutcome.REVOKE.value,
        "reason": reason,
        "validation_latency_ms": 0.0,
        "context_version": version,
        "context_trusted": trusted,
        "duplicate": False,
    }


def _validate_now(intent: ActionIntent, **snapshot) -> IntentRecord:
    """Validate at t_v; a DEGRADED intent is revalidated at once, on the same snapshot.

    HTTP has no background worker, so nothing is left on the internal queue: a
    degraded intent returns AUTHORIZED (or REVOKED) with its transformed payload
    in the same response, and a DELAYED one is revalidated when the producer
    resubmits the same id."""
    record = runtime.process_intent(intent, requeue=False, **snapshot)
    if record.state == IntentState.DEGRADED:
        record = runtime.process_intent(record.intent, requeue=False, **snapshot)
    return record


def _payload(record: IntentRecord) -> dict:
    p = record.intent.payload
    return {"action_type": p.action_type, "target_entity": p.target_entity, "parameters": p.parameters,
            "degradation_policy": p.degradation_policy}


@app.post("/v1/intents/batch")
def submit_intent_batch(body: list[dict]):
    """Submit intents in one batch; conflicts on the same target are resolved by policy."""
    ctx, ver, trusted = read_snapshot()
    results = []
    intents: list[ActionIntent] = []
    for item in body:
        err = _schema_error(item)
        if err:
            results.append({"id": item.get("id"), "source": item.get("source"), "outcome": "REVOKE", "reason": f"schema_invalid:{err}"})
        elif not trusted:
            results.append({"id": item.get("id"), "source": item.get("source"), "outcome": "REVOKE", "reason": "context_store_untrusted"})
        else:
            try:
                intents.append(ActionIntent.from_dict(item))
            except (ValueError, KeyError, TypeError) as exc:
                results.append({"id": item.get("id"), "source": item.get("source"), "outcome": "REVOKE",
                                "reason": f"schema_invalid:{exc}"})

    # Resolve winners per target_entity: intents on different resources are
    # not in conflict and must not be forced into a false conflict_loser.
    by_target: dict[str, list[ActionIntent]] = {}
    for intent in intents:
        by_target.setdefault(intent.payload.target_entity, []).append(intent)
    winners: list[ActionIntent] = []
    for group in by_target.values():
        winner, losers = runtime.coordinator.resolve(group)
        for loser in losers:
            record, duplicate = runtime.admit(loser)
            if not duplicate:
                runtime.lifecycle.transition(loser.id, IntentState.REVOKED, DecisionOutcome.REVOKE, "conflict_loser")
            results.append({"id": loser.id, "source": loser.source, "outcome": record.outcome.value if record.outcome else None, "reason": record.reason})
        if winner is not None:
            winners.append(winner)
    for winner in winners:
        record, duplicate = runtime.admit(winner)
        if not duplicate:
            record = _validate_now(winner, context=ctx, context_version=ver)
            if record.state == IntentState.AUTHORIZED:
                # The batch endpoint authorizes only; no release report follows,
                # so the target lock taken at authorization is released here.
                runtime.coordinator.release(record.intent)
        results.append({
            "id": winner.id,
            "source": winner.source,
            "outcome": record.outcome.value if record.outcome else None,
            "reason": record.reason,
        })
    executed_per_target: dict[str, int] = {}
    for intent in intents:
        rec = runtime.lifecycle.get(intent.id)
        if rec is not None and rec.outcome == DecisionOutcome.EXECUTE:
            key = intent.payload.target_entity
            executed_per_target[key] = executed_per_target.get(key, 0) + 1
    return {
        "results": results,
        # Conflict violations: targets on which more than one intent was authorized.
        "cv": sum(1 for n in executed_per_target.values() if n > 1),
        "winner": winners[0].source if len(winners) == 1 else None,
        "winners": [w.source for w in winners],
        "context_version": ver,
    }


@app.post("/v1/intents")
def submit_intent(body: dict):
    schema_err = _schema_error(body)
    if schema_err:
        _, ver, trusted = read_snapshot()
        if not trusted:
            return _rejection(body.get("id"), "context_store_untrusted", ver, False)
        return _rejection(body.get("id"), f"schema_invalid:{schema_err}", ver, trusted)
    try:
        intent = ActionIntent.from_dict(body)
    except (ValueError, KeyError, TypeError) as exc:
        _, ver, trusted = read_snapshot()
        return _rejection(body.get("id"), f"schema_invalid:{exc}", ver, trusted)
    # A duplicate id returns the retained record and is never re-validated,
    # so it cannot re-acquire the target nor trigger a second release. The one
    # exception is a DELAYED record (never authorized): resubmitting its id
    # revalidates it on a fresh snapshot.
    trusted = True
    read_values: dict = {}
    record, duplicate = runtime.admit(intent)
    if duplicate and record.state == IntentState.DELAYED:
        intent, duplicate = record.intent, False
    if not duplicate:
        # Register the intent's predicates (policy included) for truth-change
        # versioning, then validate on the snapshot read in the same step.
        doc = store.register_and_snapshot(runtime.prepare(intent))
        if not doc["trusted"]:
            runtime.lifecycle.transition(intent.id, IntentState.REVOKED, DecisionOutcome.REVOKE, "context_store_untrusted")
            return _rejection(body.get("id"), "context_store_untrusted", doc["version"], False)
        runtime.install_context_snapshot(doc["context"], doc["version"], replace=True)
        record = _validate_now(intent, context=doc["context"], context_version=doc["version"],
                               path_versions=doc["path_versions"], predicate_versions=doc["predicate_versions"])
        read_values = {p: v for p in record.read_set if not isinstance(v := get_path(doc["context"], p), type(_MISSING))}
    return {
        "id": intent.id,
        "state": record.state.value,
        "outcome": record.outcome.value if record.outcome else None,
        "reason": record.reason,
        "validation_latency_ms": record.validation_latency_ms,
        # Version of the snapshot this decision was validated against (v).
        "context_version": record.context_version,
        "read_set": record.read_set,
        "read_set_version": record.read_set_version,
        "policy_predicates": record.policy_predicates,
        "predicates": record.predicates,
        "predicate_version": record.predicate_version,
        # values of the read-set paths in the snapshot validated at t_v (audit / actuator witness)
        "read_values": read_values,
        "context_trusted": trusted,
        "duplicate": duplicate,
        # The payload XAIR authorized: after a degradation policy it differs from
        # the submitted one, and it is the payload the gateway must release.
        "authorized_payload": _payload(record) if record.state == IntentState.AUTHORIZED else None,
    }


class PublicationReport(BaseModel):
    published: bool
    reason: str
    context_version: int | None = None
    read_set_version: int | None = None


class ReleaseReport(BaseModel):
    released: bool
    reason: str
    context_version: int | None = None
    read_set_version: int | None = None
    # optional effect report sent with the release (one round trip when the
    # gateway already knows the controller's answer, or that none exists)
    effect_status: EffectStatus | None = None
    effect_detail: str = ""


def _state_doc(intent_id: str, record: IntentRecord) -> dict:
    return {
        "id": intent_id,
        "state": record.state.value,
        "outcome": record.outcome.value if record.outcome else None,
        "release_decision": record.release_decision.value if record.release_decision else None,
        "commit_seq": record.commit_seq,
        "effect_status": record.effect_status.value if record.effect_status else None,
        "reason": record.reason,
    }


def _release(intent_id: str, released: bool, reason: str, context_version, read_set_version) -> dict:
    try:
        record = runtime.report_release(intent_id, released, reason, context_version=context_version,
                                        read_set_version=read_set_version)
    except KeyError:
        raise HTTPException(status_code=404, detail="intent not found")
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _state_doc(intent_id, record)


@app.post("/v1/intents/{intent_id}/release")
def report_release(intent_id: str, body: ReleaseReport):
    """Gateway report of the release step (t_m): RELEASED or WITHHELD.

    From AUTHORIZED (optimistic mode) it carries the version observed at the
    gate (t_g); a version that differs from the one recorded at t_v turns the
    report into WITHHELD. From COMMITTED (atomic mode) it records whether the
    committed command was handed to middleware or withheld. Any other state is
    rejected with 409 and the rejected report is audited. A released intent is
    not yet executed: its effect is reported separately (POST .../effect).
    """
    doc = _release(intent_id, body.released, body.reason, body.context_version, body.read_set_version)
    if body.effect_status is not None and doc["state"] == IntentState.RELEASED.value:
        return report_effect(intent_id, EffectReport(status=body.effect_status, detail=body.effect_detail))
    return doc


@app.post("/v1/intents/{intent_id}/publication", deprecated=True)
def report_publication(intent_id: str, body: PublicationReport):
    """Deprecated alias of POST /v1/intents/{id}/release (``published`` = ``released``)."""
    return _release(intent_id, body.published, body.reason, body.context_version, body.read_set_version)


class EffectReport(BaseModel):
    status: EffectStatus
    detail: str = ""


@app.post("/v1/intents/{intent_id}/effect")
def report_effect(intent_id: str, body: EffectReport):
    """Actuator or controller report of the effect (t_a) of a released intent.

    ``confirmed`` -> EFFECT_CONFIRMED, ``failed`` -> FAILED (e.g. a
    version-conditional command the controller refused), ``unknown`` ->
    UNKNOWN_EFFECT (no feedback available; a later report can resolve it)."""
    try:
        record = runtime.report_effect(intent_id, body.status, body.detail)
    except KeyError:
        raise HTTPException(status_code=404, detail="intent not found")
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _state_doc(intent_id, record)


_COMMIT_REASONS = {
    "committed": "authorization_committed",
    "changed": "read_set_version_changed_at_commit",
    "expired": "age_bound_exceeded_at_commit",
    "duplicate": "duplicate_commit_already_logged",
    "future_skew": "decision_ahead_of_store_clock_at_commit",
    "unavailable": "context_untrusted_at_commit",
}


class CommitRequest(BaseModel):
    scope: Literal["readset", "truth"] = "readset"
    # Subtracted from the release bound to leave room for (t_c, t_m]. It can only
    # tighten the deadline: negative, infinite, or NaN margins are rejected (422).
    margin_ms: float = Field(0.0, ge=0.0, allow_inf_nan=False)


@app.post("/v1/intents/{intent_id}/commit")
def commit_authorization(intent_id: str, body: CommitRequest | None = None):
    """Atomic authorization commit (t_c).

    The read-set version comparison, the age check on the store clock, and the
    append of the authorization record to the actuation log are one atomic
    store operation, serialized with every context update. A committed record
    is an authorization, not an actuation: the intent becomes COMMITTED, the
    middleware call (t_m) and the actuator's effect (t_a) follow it and are
    reported separately (POST .../release, POST .../effect), and a context
    change after t_c is a post-commit invalidation that this operation does not
    observe. A refused commit makes the intent WITHHELD.
    """
    record = runtime.lifecycle.get(intent_id)
    if record is None:
        raise HTTPException(status_code=404, detail="intent not found")
    if record.state != IntentState.AUTHORIZED:
        raise HTTPException(status_code=409, detail=f"{intent_id}: {record.state.value} cannot be committed")
    temporal = runtime.temporal
    skew_ok, skew_reason = temporal.validate_release(record.intent)
    if not skew_ok and skew_reason.startswith("future_skew"):
        final = runtime.record_commit(intent_id, False, f"{skew_reason}_at_commit")
        return {"id": intent_id, "committed": False, "reason": final.reason, "status": "future_skew",
                "state": final.state.value, "commit_version": None}
    body = body or CommitRequest()
    truth = body.scope == "truth"
    res = store.commit_authorization(
        intent_id, record.predicates if truth else record.read_set,
        record.predicate_version if truth else record.read_set_version,
        {"action_type": record.intent.payload.action_type, "target_entity": record.intent.payload.target_entity,
         "parameters": record.intent.payload.parameters, "read_set": record.read_set,
         "read_set_version": record.read_set_version, "predicates": record.predicates,
         "predicate_version": record.predicate_version, "scope": "truth" if truth else "readset",
         "decision_epoch_ms": temporal.decision_epoch_ms(record.intent),
         "release_bound_ms": temporal.release_bound_ms(record.intent) - temporal.clock_uncertainty_ms},
        decision_epoch_ms=temporal.decision_epoch_ms(record.intent),
        age_bound_ms=temporal.release_bound_ms(record.intent) - temporal.clock_uncertainty_ms - body.margin_ms,
        max_ahead_ms=temporal.clock_uncertainty_ms,
        scope="truth" if truth else "readset",
    )
    # "duplicate": this id's authorization is already in the log (an earlier,
    # successful commit), so the intent is committed, idempotently.
    committed = res["status"] in ("committed", "duplicate")
    reason = _COMMIT_REASONS[res["status"]]
    if truth and res["status"] == "changed":
        reason = "predicate_truth_changed_at_commit"
    final = runtime.record_commit(intent_id, committed, reason, seq=res.get("seq") if committed else None)
    return {
        "id": intent_id,
        # an authorization in the actuation log: not a release, not an effect
        "committed": committed,
        "status": res["status"],
        "reason": final.reason,
        "state": final.state.value,
        "expected_read_set_version": record.read_set_version,
        "observed_read_set_version": res["observed"],
        "commit_version": res["commit_version"],
        "seq": res["seq"],
        "store_time_ms": res["store_time_ms"],
        "age_at_commit_ms": res["age_at_commit_ms"],
        "age_bound_ms": temporal.release_bound_ms(record.intent),
        "commit_mono_ms": list(res["mono_ms"]),
    }


class WithheldReport(BaseModel):
    reason: str


@app.post("/v1/intents/{intent_id}/withheld")
def report_withheld(intent_id: str, body: WithheldReport):
    """A committed authorization whose release the gateway withheld (e.g. its deadline passed before t_m).

    Appends a tombstone to the actuation log for audit. The tombstone follows the
    authorization record, so a consumer that reads the log in order does not skip
    that record because of it; ``recheck_at_apply`` is what withholds a record
    whose version or deadline no longer holds.

    Only a COMMITTED intent can be withheld: an unknown id is 404 and any other
    state 409, and in both cases nothing is written to the log. A replay on an
    intent already withheld after its commit is idempotent (no second tombstone)."""
    with runtime._lock:
        record = runtime.lifecycle.get(intent_id)
        if record is None:
            raise HTTPException(status_code=404, detail="intent not found")
        if record.state == IntentState.WITHHELD and record.commit_seq is not None:
            return {"id": intent_id, "withheld": True, "state": record.state.value, "duplicate": True}
        if record.state != IntentState.COMMITTED:
            raise HTTPException(status_code=409, detail=f"{intent_id}: {record.state.value} cannot be withheld")
        store.append_actuation({"intent_id": intent_id, "withheld": True, "reason": body.reason})
        state = runtime.report_release(intent_id, False, body.reason).state.value
    return {"id": intent_id, "withheld": True, "state": state, "duplicate": False}


@app.get("/v1/actuations")
def list_actuations(start: int = 0):
    """Committed authorization records from 0-based log position ``start``, in commit order."""
    return {"start": start, "records": store.actuation_log(start)}


@app.get("/v1/intents/{intent_id}")
def get_intent(intent_id: str):
    record = runtime.lifecycle.get(intent_id)
    if not record:
        raise HTTPException(status_code=404, detail="intent not found")
    return {**_state_doc(intent_id, record), "context_version": record.context_version}


@app.delete("/v1/intents/{intent_id}")
def revoke_intent(intent_id: str):
    """Supervisory revoke of an intent not yet committed or released (409 otherwise)."""
    try:
        record = runtime.supervisory_revoke(intent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="intent not found")
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"id": intent_id, "state": record.state.value}


@app.get("/v1/policy")
def get_policy():
    return {"policy": runtime.policy}


@app.put("/v1/policy")
def put_policy(body: dict):
    """Install server-side mandatory predicates per action type (operator configuration)."""
    return {"policy": runtime.set_policy(body)}


@app.get("/v1/metrics")
def metrics():
    from xair.adapters import runtime_state
    out = runtime.get_metrics()
    if runtime_state.OPA_URL:
        out["opa_replications"] = runtime_state.opa_replications
        out["opa_replication_errors"] = runtime_state.opa_replication_errors
    return out


@app.get("/v1/context/snapshot")
def get_context_snapshot(predicates: str | None = None, intent_id: str | None = None):
    """Snapshot with versions; ``predicates`` (JSON list) restricts predicate versions to those, read together.

    With ``intent_id`` the response also carries that intent's lifecycle state,
    read after the snapshot, so the optimistic gate learns of a supervisory revoke
    in the same request (``intent_state``; null for an unknown id)."""
    if predicates is not None:
        exprs = json.loads(predicates)
        ctx, ver, pv, trusted, io, preds = store.snapshot_with_predicates(exprs)
        if trusted:
            runtime.install_context_snapshot(ctx, ver, replace=True)
        doc = {"predicate_versions": preds}
    else:
        doc = read_snapshot_doc()
        ctx, ver, pv, trusted, io = doc["context"], doc["version"], doc["path_versions"], doc["trusted"], doc["io"]
    record = runtime.lifecycle.get(intent_id) if intent_id is not None else None
    return {"ok": True, "context": ctx, "context_version": ver, "path_versions": pv, "context_trusted": trusted,
            "predicate_versions": doc["predicate_versions"],
            "intent_state": record.state.value if record is not None else None,
            # Monotonic bounds on the store read (CLOCK_MONOTONIC, shared by processes on one kernel).
            "store_read_mono_ms": list(io) if io else None}


@app.post("/v1/context/snapshot")
def context_snapshot(body: dict):
    ver, trusted, io = update_context_store_timed(body)
    return {"ok": trusted, "context_version": ver, "context_trusted": trusted,
            "store_write_mono_ms": list(io) if io else None}
