"""Bounty 02 (#42): the rules view reads what the engine runs, on the real engine.

The probe of Sep 27: send `o26 = 7 && o27 = 9`, then `o26 = 3`. The engine runs
`o26 = 3 && o27 = 9`; the stored application_rules text still holds both units.
The running spec must say 3 and 9, never 7; a second node (a fresh process) and
a replay of the same history must give the same text and hash; and the node's
own live interpreter, fed the same units through its apply path, must report the
revision as taken with spec_revision advancing and end on that same text.

Every engine runs in a FRESH SUBPROCESS: native per-stream widths are
process-global. Auto-skips unless the native tau module is importable.
"""
import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _native_available():
    try:
        import tau_native
        tau_native.load_tau_module()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _native_available(), reason="native tau module not built")

U1 = "always ( o26[t]:bv[24] = { 7 }:bv[24] && o27[t]:bv[24] = { 9 }:bv[24] )."
U2 = "always ( o26[t]:bv[24] = { 3 }:bv[24] )."


def _genesis_history():
    with open(os.path.join(REPO, "data", "genesis.json")) as fh:
        rules = json.load(fh).get("application_rules") or ""
    if not rules.strip():
        pytest.skip("no genesis artifact (run gen_genesis.py as CI does)")
    return rules.strip()


def _env():
    import tau_native
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [p for p in (tau_native.native_tau_dir(), REPO, env.get("PYTHONPATH")) if p])
    env["TAU_ENV"] = "test"
    return env


def _child(body):
    proc = subprocess.run([sys.executable, "-c", body], capture_output=True, text=True,
                          env=_env(), cwd=REPO, timeout=240)
    line = next((l for l in proc.stdout.splitlines() if l.startswith("RESULT ")), None)
    assert line, f"no result\nSTDOUT:\n{proc.stdout[-2000:]}\nSTDERR:\n{proc.stderr[-2000:]}"
    return json.loads(line[len("RESULT "):])


def _running(history):
    import tau_native
    return tau_native.running_spec_subprocess("\n".join(history), 120)


def test_the_probe_of_sep_27_reads_what_the_engine_runs():
    g = _genesis_history()
    spec = _running([g, U1, U2])
    text = spec["text"]
    assert "o26[t]:bv[24] = { 3 }:bv[24]" in text
    assert "o27[t]:bv[24] = { 9 }:bv[24]" in text
    assert "{ 7 }" not in text           # the displaced fragment is gone
    assert spec["spec_revision"] == 2    # both units changed the spec
    # The submitted text still holds o26 = 7: that is why it is not the view.
    assert "{ 7 }" in "\n".join([g, U1, U2])


def test_a_second_node_and_a_replay_give_the_same_text_and_hash():
    g = _genesis_history()
    first = _running([g, U1, U2])
    second = _running([g, U1, U2])              # another process: another node
    replay = _running([g, U1]), _running([g, U1, U2])  # block by block, from block 0
    assert first == second == replay[1]
    assert replay[0]["sha256"] != first["sha256"]
    import hashlib
    assert first["sha256"] == hashlib.sha256(first["text"].encode()).hexdigest()


def test_the_live_apply_path_reports_the_revision_and_ends_on_the_same_spec(tmp_path):
    """The node's own interpreter, fed U1 then U2 as apply does (i0, rules
    update): each revision is ACCEPTED_CHANGED with spec_revision advancing, and
    its current spec equals the rebuilt running spec."""
    g = _genesis_history()
    seed = tmp_path / "seed.tau"
    seed.write_text(g)
    body = r'''
import json, tau_native
iface = tau_native.TauInterface(%r)
receipts = []
for unit in (%r, %r):
    iface.communicate(rule_text=unit, target_output_stream_index=0)
    r = iface.last_revision_receipt
    receipts.append({"outcome": r["outcome"], "before": r["spec_revision_before"],
                     "after": r["spec_revision_after"]})
print("RESULT " + json.dumps({"receipts": receipts,
                               "spec": iface.interpreter.current_spec()}))
''' % (str(seed), U1, U2)
    live = _child(body)
    assert [r["outcome"] for r in live["receipts"]] == ["ACCEPTED_CHANGED"] * 2
    assert [(r["before"], r["after"]) for r in live["receipts"]] == [(0, 1), (1, 2)]
    rebuilt = _running([g, U1, U2])
    import tau_native
    assert tau_native.TauInterface._ensure_trailing_period(live["spec"]) == rebuilt["text"]
