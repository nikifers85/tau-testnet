"""Outcome of every confirmed transaction and the rule effects it had (bounty 02, #42).

Block apply records, per transaction: applied / skipped / rejected, the reason,
the fee charged, bounded logs, and which clauses it registered, replaced or
revoked (with the sha256 of the stored body). The node keeps them per block and
gettxstatus serves them beside `status: confirmed`, which keeps its meaning.
"""
import hashlib
import json
import types

import pytest

import db
from bls_keys import SK_ALICE, SK_BOB, pubkey_hex
from commands import gettxstatus
from consensus import sender_policy
from consensus.engine import _rule_effect
from test_clause_registration import (
    BLOCK_BODY, BLOCK_RULE, LIMIT_BODY, LIMIT_RULE, Chain, offer_to, accept_tx,
    routed_tx, transfer_tx, verdict_evaluator,
)

ALICE_KEY = (SK_ALICE, pubkey_hex(SK_ALICE))
BOB_KEY = (SK_BOB, pubkey_hex(SK_BOB))
REVOKE_RULE = "always ( (o5[t]:bv[24] = { #x000001 }:bv[24]) )."


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def chain():
    sender_policy.set_evaluator("apply", verdict_evaluator("apply"))
    return Chain([ALICE_KEY, BOB_KEY])


def outcomes(result):
    return result.next_snapshot.metadata["tx_outcomes"]


# --- effects --------------------------------------------------------------------


def test_register_replace_revoke_are_recorded_with_body_hash(chain):
    sk, pk = BOB_KEY
    r1 = chain.apply([routed_tx(sk, pk, LIMIT_RULE, seq=0)])
    (o1,) = outcomes(r1)
    assert o1["outcome"] == "applied"
    assert o1["rule_effects"] == [{"account": pk, "kind": "clause", "stream": 5,
                                   "action": "registered", "sha256": sha(LIMIT_BODY)}]

    r2 = chain.apply([routed_tx(sk, pk, BLOCK_RULE, seq=1)])
    assert outcomes(r2)[0]["rule_effects"][0]["action"] == "replaced"
    assert outcomes(r2)[0]["rule_effects"][0]["sha256"] == sha(BLOCK_BODY)

    r3 = chain.apply([routed_tx(sk, pk, REVOKE_RULE, seq=2)])
    eff = outcomes(r3)[0]["rule_effects"][0]
    assert eff["action"] == "revoked" and eff["sha256"] == sha(BLOCK_BODY)
    assert chain.clause(pk) is None


def test_the_hash_matches_what_is_in_force(chain):
    """The wallet check: hash in rule_effects == hash of the clause in the registry."""
    sk, pk = BOB_KEY
    r = chain.apply([routed_tx(sk, pk, LIMIT_RULE, seq=0)])
    assert outcomes(r)[0]["rule_effects"][0]["sha256"] == sha(chain.clause(pk))


def test_offer_acceptance_records_its_effect(chain):
    sk, pk = BOB_KEY
    offer = offer_to(chain, pk, LIMIT_RULE)
    r = chain.apply([accept_tx(sk, offer, seq=0)])
    (o,) = outcomes(r)
    assert o["rule_effects"] and o["rule_effects"][0]["account"] == pk
    assert o["rule_effects"][0]["action"] == "registered"
    assert o["rule_effects"][0]["sha256"] == sha(chain.clause(pk))


def test_a_plain_transfer_has_no_effect_and_is_applied(chain):
    sk, pk = ALICE_KEY
    (o,) = outcomes(chain.apply([transfer_tx(sk, pk, 10, seq=0)]))
    assert o["outcome"] == "applied" and o["rule_effects"] == []
    assert o["tx_hash"] and len(o["tx_hash"]) == 64


def test_logs_are_bounded(chain):
    sk, pk = ALICE_KEY
    (o,) = outcomes(chain.apply([transfer_tx(sk, pk, 10, seq=0)]))
    assert len(o["logs"]) <= 16 and all(len(line) <= 400 for line in o["logs"])


@pytest.mark.parametrize("before,after,action", [
    (None, "a", "registered"), ("a", "b", "replaced"), ("a", None, "revoked"),
])
def test_rule_effect_shapes(before, after, action):
    eff = _rule_effect("k" * 96, 5, before, after)
    assert eff["action"] == action
    assert _rule_effect("k" * 96, 5, "a", "a") is None
    assert _rule_effect("k" * 96, 0xFFFFFFFF, None, "g")["kind"] == "guard"


# --- persistence and gettxstatus ---------------------------------------------------


def test_record_and_read_back(temp_database):
    outs = [{"index": 0, "tx_hash": "ab" * 32, "outcome": "applied", "reason": None,
             "fee_charged": 10, "rule_effects": [{"account": "c" * 96, "kind": "clause",
                                                  "stream": 5, "action": "registered",
                                                  "sha256": "d" * 64}],
             "logs": ["Rule applied"]}]
    db.record_tx_outcomes("ee" * 32, 7, outs, [{"account": "c" * 96, "kind": "guard",
                                                 "stream": 0xFFFFFFFF, "action": "revoked",
                                                 "sha256": "f" * 64, "request_id": "01" * 32}])
    got = db.get_tx_outcome("ee" * 32, "ab" * 32)
    assert got["outcome"] == "applied" and got["fee_charged"] == 10
    assert got["rule_effects"][0]["action"] == "registered"
    assert db.get_tx_outcome("ee" * 32, "00" * 32) is None
    with db._db_lock:
        rows = db._db_conn.execute(
            "SELECT action, request_id FROM rule_effects ORDER BY action").fetchall()
    assert rows == [("registered", None), ("revoked", "01" * 32)]
    # Re-recording the same block does not duplicate effects.
    db.record_tx_outcomes("ee" * 32, 7, outs, [])
    with db._db_lock:
        assert db._db_conn.execute("SELECT COUNT(*) FROM rule_effects").fetchone()[0] == 1


def _confirmed_block():
    import block as block_module
    from test_gettxstatus import _set_canonical_head

    tx = {"tx_id": "t", "operations": {"1": [["a" * 96, "b" * 96, "1"]]}}
    genesis = block_module.Block.create(block_number=0, previous_hash="00" * 32,
                                        transactions=[], proposer_pubkey="a" * 96,
                                        timestamp=1_700_000_000)
    blk = block_module.Block.create(block_number=1, previous_hash=genesis.block_hash,
                                    transactions=[tx], proposer_pubkey="a" * 96,
                                    timestamp=1_700_000_001)
    db.add_block(genesis)
    db.add_block(blk)
    _set_canonical_head(blk.block_hash, 1)
    return blk


def _status(tx_hash):
    resp = json.loads(gettxstatus.execute(f"gettxstatus {tx_hash}", types.SimpleNamespace(db=db)))
    assert resp["status"] == "ok", resp
    return resp["data"]


def test_gettxstatus_serves_the_outcome_beside_confirmed(temp_database):
    blk = _confirmed_block()
    tx_hash = blk.tx_ids[0]
    db.record_tx_outcomes(blk.block_hash, 1, [{
        "index": 0, "tx_hash": tx_hash, "outcome": "skipped", "reason": None,
        "fee_charged": 10, "rule_effects": [], "logs": ["Vote ignored (valid no-op)"]}])
    data = _status(tx_hash)
    assert data["status"] == "confirmed"          # unchanged meaning
    assert data["outcome"] == "skipped" and data["fee_charged"] == 10
    assert data["tx_index"] == 0 and data["logs"] == ["Vote ignored (valid no-op)"]


def test_a_block_applied_before_outcomes_answers_not_recorded(temp_database):
    blk = _confirmed_block()
    data = _status(blk.tx_ids[0])
    assert data["status"] == "confirmed" and data["outcome"] == "not_recorded"
    assert "rule_effects" not in data


from test_retractable_reorg import RetractableReorgTestCase, funding  # noqa: E402


class TestOutcomesWrittenAtCommit(RetractableReorgTestCase):
    """End to end through chain_state: committed blocks' outcomes are queryable,
    keyed by block, so a reorg needs no cleanup and the winner answers."""

    def test_committed_and_reorged_blocks(self):
        from block import compute_tx_hash

        common = self.blocks(self.genesis_hash, [(1, [funding()])])
        fork_a = self.blocks(common[-1], [(2, [])])
        fork_b = self.blocks(common[-1], [(5, []), (6, [])])
        self.adopt(common + fork_a)
        tx_hash = compute_tx_hash(common[0].transactions[0])
        got = db.get_tx_outcome(common[0].block_hash, tx_hash)
        self.assertIsNotNone(got)
        self.assertEqual(got["outcome"], "applied")
        self.assertEqual(got["tx_index"], 0)

        self.reorg_to(fork_b)
        # The funding tx stays canonical in `common`: still answered.
        self.assertEqual(db.get_tx_outcome(common[0].block_hash, tx_hash)["outcome"], "applied")
