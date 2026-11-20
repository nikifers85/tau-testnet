"""The specification the engine runs at the tip (bounty 02, #42).

Usage: ``getrunningspec``. Returns ``running_spec`` -- the engine's own
``current_spec()`` after the committed rule history, with its sha256 and the
engine's revision counter -- beside the block it was read at and the hash of
the stored rule history. ``application_rules`` (getgovernance) stays the
append-only text that was submitted; this is what is in force. Same text and
hash on every node at the same tip and after a replay from block 0.
"""
import api_response
import rules_view

_CMD = "getrunningspec"


def execute(raw_command: str, container):
    if len(raw_command.split()) != 1:
        return api_response.error_response(_CMD, f"Usage: {_CMD}", "INVALID_PARAMS")
    chain_state = container.chain_state
    db = container.db
    # One tip: the stored history and the block it belongs to are read together.
    with chain_state._balance_lock, chain_state._sequence_lock, chain_state._rules_lock:
        head = db.get_canonical_head_block() or {}
        app = (chain_state._application_rules_state or "").strip()
        cons = (chain_state._consensus_rules_state or "").strip()
    history = app if app else cons
    header = head.get("header") or {}
    spec = rules_view.running_spec(history)
    return api_response.success_response(_CMD, {
        "block_hash": head.get("block_hash", ""),
        "height": int(header.get("block_number", 0) or 0),
        "history_sha256": spec.pop("history_sha256"),
        "running_spec": spec,
    })
