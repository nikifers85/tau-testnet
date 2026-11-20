# Rules in force: what a wallet can rely on

Issue: [IDNI/tau-testnet#42](https://github.com/IDNI/tau-testnet/issues/42).

## Did my transaction take effect? (`gettxstatus`)

For a transaction in a canonical block, `gettxstatus` keeps `status: confirmed`
(meaning: it is in a block) and adds what the node decided when it applied it:

| Field | Meaning |
|---|---|
| `outcome` | `applied` — the transaction did what it asked; `skipped` — it is in the block and paid its fee, but was a valid no-op (an offer that lost a race, a duplicate request, an ignored decision or vote); `rejected` — refused at apply (seen when verifying another proposer's block); `not_recorded` — this node applied the block before outcomes were recorded, so it does not know |
| `tx_index` | position in the block |
| `reason` | machine-readable reason when not applied (`rule_not_applied`, `user_policy_block`, …) |
| `fee_charged` | fee actually charged |
| `rule_effects` | every clause the transaction changed: `{account, kind: clause \| guard \| application_unit, stream, action: registered \| replaced \| revoked \| accumulated, sha256}`; an `application_unit` also carries the engine's verdict (below) |
| `logs` | the apply log lines, at most 16 of 400 characters — for people, never part of any committed value |

`sha256` is the hash of the clause body as stored in the per-account registry
(for `application_unit`, of the submitted unit text).

### "Your protection is on"

A wallet may say the account's own o5 rule is in force when **all** hold:

1. its rule transaction is `confirmed` with `outcome: applied`;
2. one `rule_effects` entry has `account` = the wallet's key, `kind: clause`,
   `stream: 5`, `action: registered` or `replaced`;
3. that entry's `sha256` equals the sha256 of the clause the wallet sent, after
   the node's canonical form (`clause_body_v1`).

A `skipped` outcome, a different hash or no entry means it is **not** on.
A later transaction can replace or revoke the clause; check the newest effect
for the account.

Rule-change requests released at the end of a block record their effects too,
with the `request_id` instead of a transaction hash.

### Did the engine take the revision?

An `application_unit` effect carries what the engine itself reported for that
unit, so a wallet does not have to re-run the engine:

| Field | Meaning |
|---|---|
| `revision` | `taken` — the engine took the revision and its specification changed; `noop` — accepted, nothing changed; `unknown` — the engine binding gives no counter |
| `engine_outcome` | the engine's verdict (`ACCEPTED_CHANGED`, `ACCEPTED_NOOP`) |
| `spec_revision` | the engine's revision counter after the unit (`spec_revision_before`: before it) |

A unit the engine rejects never reaches a block (`reason: rule_rejected`).

## What is in force in the shared spec? (`getrunningspec`)

`application_rules` is the append-only text that was submitted. Pointwise
revision is not append-only: send `o26 = 7 && o27 = 9`, then `o26 = 3`, and the
engine runs `o26 = 3 && o27 = 9` while the stored text still shows `o26 = 7`.

`getrunningspec` returns what the engine runs:

```json
{"block_hash": "...", "height": 12, "history_sha256": "...",
 "running_spec": {"status": "ok", "text": "always ... && o27[t]:bv[24] = { 9 }:bv[24] && o26[t]:bv[24] = { 3 }:bv[24].",
                  "sha256": "...", "spec_revision": 2}}
```

It is the engine's own `current_spec()` after the committed rule history,
rebuilt in a throwaway interpreter the way the node rebuilds its own, in a child
process (native stream widths are process-global, so the live interpreter is
never touched). It is a pure function of the history, cached by its hash, so
every node at the same tip and a replay from block 0 give the same text and
`sha256`. `status: unavailable` (with a `reason`) means this node could not run
the engine; it never falls back to the stored text.

Tests: `tests/test_running_spec_native.py` (the probe above on the real engine;
a second process and a block-by-block replay give the same text and hash; the
live apply path reports `ACCEPTED_CHANGED` with `spec_revision` 0→1→2 and ends
on the same text), `tests/test_rules_view.py`.

## What a wallet may not conclude

- An `application_unit` effect proves the unit was **accumulated** and whether
  the engine changed its spec, not that every part of it is still in force:
  read `getrunningspec` for that. Per-account registry clauses are exact.
- Outcomes are node-local records computed identically on every node, but a
  light client still trusts the node it asks.
