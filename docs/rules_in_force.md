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
| `rule_effects` | every clause the transaction changed: `{account, kind: clause \| guard \| application_unit, stream, action: registered \| replaced \| revoked \| accumulated, sha256}` |
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

## What a wallet may not conclude

- An `application_unit` effect proves the unit was **accumulated**, not that
  every part of it is still in force: pointwise revision lets a later unit
  displace part of an earlier one. Only per-account registry clauses are exact.
- Outcomes are node-local records computed identically on every node, but a
  light client still trusts the node it asks.
