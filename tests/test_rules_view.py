"""Bounty 02 (#42): the running spec served next to the stored rule text,
and the engine's verdict on every confirmed rule unit. The real engine runs in
tests/test_running_spec_native.py; here the worker is stubbed."""
import json
import types

import pytest

import rules_view
from commands import getrunningspec
from consensus.engine import _application_unit_effect


@pytest.fixture(autouse=True)
def _fresh_cache():
    rules_view.clear_cache()
    yield
    rules_view.clear_cache()


def _spec(text, rev):
    import hashlib
    return {"text": text, "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "spec_revision": rev}


def test_running_spec_is_cached_by_history(monkeypatch):
    calls = []

    def worker(history, timeout):
        calls.append(history)
        return _spec("always (o26 = 3 && o27 = 9).", 2)

    monkeypatch.setattr("tau_native.running_spec_subprocess", worker)
    a = rules_view.running_spec("g\nu1\nu2")
    b = rules_view.running_spec("g\nu1\nu2")
    assert a == b and a["status"] == "ok" and len(calls) == 1
    assert a["history_sha256"] == rules_view.history_sha256("g\nu1\nu2")
    rules_view.running_spec("g\nu1")
    assert len(calls) == 2


def test_an_unavailable_engine_is_reported_and_not_cached(monkeypatch):
    def broken(history, timeout):
        raise RuntimeError("native tau unavailable")

    monkeypatch.setattr("tau_native.running_spec_subprocess", broken)
    out = rules_view.running_spec("g")
    assert out["status"] == "unavailable" and "unavailable" in out["reason"]
    monkeypatch.setattr("tau_native.running_spec_subprocess",
                        lambda h, t: _spec("always (T).", 0))
    assert rules_view.running_spec("g")["status"] == "ok"


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _container(app_rules, head):
    cs = types.SimpleNamespace(_balance_lock=_Lock(), _sequence_lock=_Lock(),
                               _rules_lock=_Lock(), _application_rules_state=app_rules,
                               _consensus_rules_state="cons")
    return types.SimpleNamespace(chain_state=cs, db=types.SimpleNamespace(
        get_canonical_head_block=lambda: head))


def test_getrunningspec_serves_the_engine_text_at_a_named_block(monkeypatch):
    seen = []

    def worker(history, timeout):
        seen.append(history)
        return _spec("always (o26 = 3 && o27 = 9).", 2)

    monkeypatch.setattr("tau_native.running_spec_subprocess", worker)
    head = {"block_hash": "ab" * 32, "header": {"block_number": 7}}
    out = json.loads(getrunningspec.execute("getrunningspec", _container("g\nu1\nu2\n", head)))
    data = out["data"]
    assert data["block_hash"] == "ab" * 32 and data["height"] == 7
    assert data["running_spec"]["status"] == "ok"
    assert data["running_spec"]["text"] == "always (o26 = 3 && o27 = 9)."
    assert data["running_spec"]["spec_revision"] == 2
    assert seen == ["g\nu1\nu2"]          # the stored history, stripped
    assert data["history_sha256"] == rules_view.history_sha256("g\nu1\nu2")


def test_getrunningspec_takes_no_arguments():
    out = json.loads(getrunningspec.execute("getrunningspec x", _container("", {})))
    assert out["status"] == "error" and out["error"]["code"] == "INVALID_PARAMS"


@pytest.mark.parametrize("outcome,revision", [
    ("ACCEPTED_CHANGED", "taken"), ("ACCEPTED_NOOP", "noop"), (None, "unknown"),
])
def test_an_application_unit_effect_carries_the_engine_verdict(outcome, revision):
    receipt = None if outcome is None else {
        "outcome": outcome, "spec_revision_before": 1, "spec_revision_after": 2}
    eff = _application_unit_effect("aa" * 48, "always (o26 = 3).", receipt)
    assert eff["kind"] == "application_unit" and eff["action"] == "accumulated"
    assert eff["revision"] == revision and eff["engine_outcome"] == outcome
    if receipt:
        assert (eff["spec_revision_before"], eff["spec_revision"]) == (1, 2)
    else:
        assert eff["spec_revision"] is None
