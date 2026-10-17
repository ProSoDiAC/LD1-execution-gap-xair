"""A stop fences the intents already validated on its target (stop epoch, paper E1/E9 discussion)."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

os.environ.setdefault("REDIS_URL", "")

from fastapi.testclient import TestClient  # noqa: E402

from xair.adapters.http_server import _stop_key, app, store  # noqa: E402
from xair.core.versioning import read_set_version  # noqa: E402

client = TestClient(app)
POLICY = {"RESUME": {"class": "start"}, "STOP": {"class": "stop"}}


def _intent(action, target):
    return {"id": str(uuid.uuid4()), "source": "ai", "timestamp_decision": datetime.now(timezone.utc).isoformat(),
            "freshness_window_ms": 5000, "preconditions": [{"expr": "line.state == 'RUN'"}],
            "payload": {"action_type": action, "target_entity": target}}


def setup_function():
    client.put("/v1/policy", json=POLICY)
    client.post("/v1/context/snapshot", json={"line": {"state": "RUN"}})


def teardown_function():
    client.put("/v1/policy", json={})


def test_resume_validated_before_a_stop_sees_the_stop_epoch_change():
    target = f"t_{uuid.uuid4().hex[:6]}"
    key = _stop_key(target)
    resume = client.post("/v1/intents", json=_intent("RESUME", target)).json()
    assert resume["outcome"] == "EXECUTE" and f"stop.{key}" in resume["read_set"]
    before = read_set_version(store.snapshot_full()[2], resume["read_set"])
    assert before == resume["read_set_version"]
    assert client.post("/v1/intents", json=_intent("STOP", target)).json()["outcome"] == "EXECUTE"
    after = read_set_version(store.snapshot_full()[2], resume["read_set"])
    assert after != before                      # the gate or the commit would now refuse the RESUME
    ctx = store.snapshot_full()[0]
    assert ctx["stop"][key] > 0


def test_resume_admitted_after_the_stop_is_not_fenced():
    target = f"t_{uuid.uuid4().hex[:6]}"
    client.post("/v1/intents", json=_intent("STOP", target))
    out = client.post("/v1/intents", json=_intent("RESUME", target)).json()
    assert out["outcome"] == "EXECUTE"


def test_a_stop_on_another_target_does_not_fence():
    a, b = f"t_{uuid.uuid4().hex[:6]}", f"t_{uuid.uuid4().hex[:6]}"
    resume = client.post("/v1/intents", json=_intent("RESUME", a)).json()
    client.post("/v1/intents", json=_intent("STOP", b))
    pv = store.snapshot_full()[2]
    assert read_set_version(pv, resume["read_set"]) == resume["read_set_version"]


def test_resume_decided_before_a_stop_is_revoked_even_if_it_arrives_after_it():
    target = f"t_{uuid.uuid4().hex[:6]}"
    late = _intent("RESUME", target)                      # decided now, submitted after the stop
    assert client.post("/v1/intents", json=_intent("STOP", target)).json()["outcome"] == "EXECUTE"
    out = client.post("/v1/intents", json=late).json()
    assert out["outcome"] == "REVOKE" and "stop." in out["reason"]
