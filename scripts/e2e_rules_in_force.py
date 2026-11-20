#!/usr/bin/env python3
"""End-to-end: the running spec and rule outcomes on REAL, disposable nodes (#42).

Drives live nodes with the NATIVE Tau engine through the probe of Sep 27:
one account sends `o26 = 7 && o27 = 9`, then `o26 = 3`, as ordinary user_tx
rule transactions. Then:

  1. gettxstatus on each confirmed rule transaction carries its apply outcome:
     an application_unit effect with revision "taken", ACCEPTED_CHANGED and
     spec_revision advancing by one per unit;
  2. getrunningspec reports o26 = 3 and o27 = 9 (never 7), while the stored
     application_rules text still holds the displaced o26 = 7;
  3. the proposer after a restart, and a follower that replays every block from
     genesis, return the same running spec text and sha256 at the same block.

Reuses the isolation, provisioning and node harness of scripts/e2e_retractable.py.

USAGE
    python scripts/e2e_rules_in_force.py provision --dir .local/rif-e2e
    TAU_NATIVE_PATH=<dir with the tau module> \\
        python scripts/e2e_rules_in_force.py run --dir .local/rif-e2e
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import e2e_retractable as h  # noqa: E402  (the shared harness)

h.NETWORK_ID = "tau-rif-e2e"
h.RPC_PORTS = {"node1": 64752, "node2": 64762}
h.P2P_PORTS = {"node1": 64772, "node2": 64782}
h.ACTORS = ("miner", "author", "pad1", "pad2")

say, check, ok, produce, wait_until = h.say, h.check, h.ok, h.produce, h.wait_until

U1 = "always ( o26[t]:bv[24] = { 7 }:bv[24] && o27[t]:bv[24] = { 9 }:bv[24] )."
U2 = "always ( o26[t]:bv[24] = { 3 }:bv[24] )."
IN_FORCE = ("o26[t]:bv[24] = { 3 }:bv[24]", "o27[t]:bv[24] = { 9 }:bv[24]")
DISPLACED = "{ 7 }"
TX_HASHES = []


class Rules(h.Signer):
    def rule(self, node, name, text):
        seq, tip = self._envelope(node, name)
        payload = {"tx_type": "user_tx", "sender_pubkey": self.pk(name), "sequence_number": seq,
                   "expiration_time": int(time.time()) + 3600, "expire_at_height": tip + 1000,
                   "fee_limit": "100000", "operations": {"0": text}}
        signed = self.sign(name, payload)
        return self.submit(node, signed), signed


def send_rule(node, sg, text, label):
    res, signed = sg.rule(node, "author", text)
    check(f"{label}: admitted", ok(res), json.dumps(res)[:400])
    tx_hash = (res.get("data") or {}).get("tx_hash")
    check(f"{label}: mined", produce(node, node.height() + 1))
    status = node.rpc(f"gettxstatus {tx_hash}")["data"] if tx_hash else {}
    check(f"{label}: confirmed and applied", status.get("status") == "confirmed"
          and status.get("outcome") == "applied", status)
    units = [e for e in status.get("rule_effects") or [] if e.get("kind") == "application_unit"]
    check(f"{label}: one application_unit effect", len(units) == 1, status.get("rule_effects"))
    return tx_hash, (units[0] if units else {})


def running(node):
    return node.rpc("getrunningspec")["data"]


def scenario_probe(node, sg):
    say("1-2. the probe of Sep 27 through real rule transactions")
    before = running(node)
    check("start: running spec available", before["running_spec"]["status"] == "ok", before)
    base_rev = before["running_spec"]["spec_revision"]
    h1, e1 = send_rule(node, sg, U1, "unit 1 (o26=7, o27=9)")
    h2, e2 = send_rule(node, sg, U2, "unit 2 (o26=3)")
    TX_HASHES[:] = [("unit 1", h1), ("unit 2", h2)]
    # spec_revision is the counter of the engine that applied the unit on this
    # node, so the step is what is compared, not the absolute value.
    for label, eff in (("unit 1", e1), ("unit 2", e2)):
        check(f"{label}: revision taken", eff.get("revision") == "taken"
              and eff.get("engine_outcome") == "ACCEPTED_CHANGED", eff)
        check(f"{label}: spec_revision advanced by one", isinstance(eff.get("spec_revision"), int)
              and eff.get("spec_revision") == (eff.get("spec_revision_before") or 0) + 1, eff)
    after = running(node)
    spec = after["running_spec"]
    check("running spec: o26 = 3 and o27 = 9", spec.get("status") == "ok"
          and all(s in spec.get("text", "") for s in IN_FORCE), spec)
    check("running spec: the displaced o26 = 7 is gone", DISPLACED not in spec.get("text", ""))
    check("running spec: spec_revision advanced by 2", spec.get("spec_revision") == base_rev + 2)
    stored = node.rpc("getgovernance")["data"]["application_rules"]
    check("stored application_rules still hold o26 = 7 (why it is not the view)",
          DISPLACED in stored)
    check("running spec read at the head", after["block_hash"] == node.head().get("block_hash"))
    return after


def scenario_nodes(node1, node2, at):
    say("3. restart and a follower replaying from genesis")
    node1.stop()
    node1.start()
    check("node1 restarted", node1.wait_ready())
    again = running(node1)
    check("restart: same block, text and sha256",
          (again["block_hash"], again["running_spec"].get("sha256"))
          == (at["block_hash"], at["running_spec"]["sha256"]), again)
    node1.peer_id = node1.peer_id or node1._peer_id()
    node2.bootstrap = [{"peer_id": node1.peer_id,
                        "addrs": [f"/ip4/127.0.0.1/tcp/{node1.p2p_port}"]}]
    node2.start()
    check("node2 ready", node2.wait_ready())
    check("node2 replays to the same head", wait_until(
        lambda: node2.head().get("block_hash") == at["block_hash"], "node2 head", timeout=300))
    for label, tx_hash in TX_HASHES:
        st = node2.rpc(f"gettxstatus {tx_hash}")["data"]
        units = [e for e in st.get("rule_effects") or [] if e.get("kind") == "application_unit"]
        check(f"follower: {label} applied, revision taken", st.get("outcome") == "applied"
              and units and units[0].get("revision") == "taken", st)
    other = running(node2)
    check("follower: same text and sha256 at the same block",
          (other["block_hash"], other["running_spec"].get("sha256"), other["running_spec"].get("text"))
          == (at["block_hash"], at["running_spec"]["sha256"], at["running_spec"]["text"]), other)


def run(base, log_dir, deadline):
    base = os.path.abspath(base)
    h.DEADLINE[0] = time.time() + deadline
    keys = json.load(open(os.path.join(base, "keys.json")))
    node1 = h.Node("node1", base, keys, os.path.join(log_dir, "rif-e2e-node1.log"), mining=False)
    node2 = h.Node("node2", base, keys, os.path.join(log_dir, "rif-e2e-node2.log"), mining=False)
    h.assert_isolated(base, node1.paths() + node2.paths())
    for node in (node1, node2):
        if os.path.exists(node.db_path):
            raise SystemExit(f"REFUSING: {node.db_path} already exists; re-provision for a clean run")
    import atexit
    atexit.register(h.stop_all)
    sg = Rules(keys, node1.tree)
    try:
        node1.start()
        if not node1.wait_ready():
            say("node1 did not become ready")
            return 2
        at = scenario_probe(node1, sg)
        scenario_nodes(node1, node2, at)
    finally:
        h.stop_all()
    say("")
    if h.FAILED:
        say(f"{len(h.FAILED)} check(s) FAILED:")
        for name in h.FAILED:
            say(f"  - {name}")
        return 1
    say("All checks passed.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["provision", "run"])
    parser.add_argument("--dir", default=os.path.join(h.REPO, ".local", "rif-e2e"))
    parser.add_argument("--log-dir", default=os.path.join(h.REPO, ".local"))
    parser.add_argument("--overlay-worktree", action="store_true")
    parser.add_argument("--deadline", type=int, default=1800)
    args = parser.parse_args(argv)
    os.makedirs(args.log_dir, exist_ok=True)
    if args.mode == "provision":
        return h.provision(args.dir, args.overlay_worktree)
    h.require_native()
    return run(args.dir, args.log_dir, args.deadline)


if __name__ == "__main__":
    raise SystemExit(main())
