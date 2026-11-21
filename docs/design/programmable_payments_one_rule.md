## Amendment to the Bounty 01 design: one Tau rule decides every payment

This amends my design for #34, #35 and #63 ([posted here](https://github.com/IDNI/tau-testnet/issues/34#issuecomment-7018318745)), following @andrei-idni's criterion on #146: new payment policies, and combinations of supported actions, are expressed through Tau rules and configuration, with no feature-specific execution path in the Python node. Design first: no consensus code changes until this is approved. I have marked #128 as a draft. It was built on the original design, which has four feature-specific books (grants, standing payments, routes, payouts), and that is the shape this amendment removes.

### 1. The change in one paragraph

There is one consensus object, a **payment program**: an account, an optional escrow, up to 8 named parties, a Tau rule, the triggers that run it, and bounds. Whenever a trigger fires, the node steps the rule once with a fixed set of inputs, reads the amounts the rule writes, and pays them atomically or not at all. Limits, time locks, schedules, splits, refunds and computed payouts are all **written in the rule**. The node does not know what a "grant", a "rent" or a "route" is. It knows only generic things: escrow, period counters, the block clock, three triggers, and paying legs within bounds. A new policy, or a new combination, is a new rule and needs no node change.

### 2. The object (configuration)

| Field | Meaning |
|---|---|
| `owner` | the account whose money the program moves; signs install, replace and remove |
| `fund` | optional escrow, locked from the owner at install (0 = pay from the owner's balance when the rule decides) |
| `parties[1..8]` | declared keys. Slot k is paid what the rule writes on `o<k>`. The rule chooses amounts, never addresses |
| `rule` | one Tau clause, at most 8 KiB, typed as in the tables below |
| `triggers` | any of `time`, `credit`, `execute` |
| `period` | day / ISO week / month (UTC) / fixed N seconds: what the `i2`/`i3` counters reset on |
| `max_per_evaluation` | hard cap on the total of one evaluation |
| `expires_at` | mandatory, at most 5 years ahead. At expiry the program closes and any escrow goes to `refund_to` |
| `refund_to` | default `owner` |
| `locked_until` | optional: before this date the owner cannot remove or replace the program (protects the recipient) |
| `max_fee_per_evaluation` | for time-triggered evaluations, see §5 |

### 3. What the rule reads (fixed map, host-computed from committed state)

| Stream | Value |
|---|---|
| `i1` bv[24] | amount the program can pay now: escrow left, or the owner's spendable balance if `fund = 0`, or the incoming amount for a credit trigger |
| `i2` bv[24] | total paid in the current period |
| `i3` bv[24] | evaluations that paid something in the current period |
| `i4` bv[24] | total paid since install |
| `i5` bv[64] | block timestamp (UTC seconds), the same value rules already read on `i5` |
| `i6` bv[64] | seconds since the last payment (since install if none) |
| `i7` `i8` `i9` `i10` bv[24] | weekday (0 = Monday), hour, day of month, days in this month (UTC) |
| `i11` bv[24] | trigger: 1 time, 2 credit, 3 execute |
| `i12` bv[24] | incoming credit amount (credit trigger) |
| `i13`..`i20` bv[24] | data supplied by the executor (execute trigger), for example a requested amount or a sale price |
| `i21` bv[24] | which declared party sent the execute (0 = someone else) |

The calendar values are computed by the integer-only period code already in #128 (`consensus/periods.py`). The rule gets the clock as data, so "the 31st, clamped to the month's last day" is `i9 = i10` in the rule, not a node feature.

### 4. What the rule writes

| Stream | Meaning |
|---|---|
| `o1`..`o8` bv[24] | amount to party k (unwritten = 0) |
| `o9` bv[24] | 1 = close the program now: escrow left goes to `refund_to` |
| `o10` bv[64] | time trigger: seconds until the next evaluation (minimum 3,600; 0 = no further wake) |

### 5. Triggers, the only three, and who pays

| Trigger | When | Fee |
|---|---|---|
| `time` | the end-of-block sweep, for programs whose wake time has passed, in `(wake, id)` order, at most 64 per block (the rest carry over) | owner, through the ordinary fee step, capped by the signed `max_fee_per_evaluation`; an owner who cannot pay is closed and refunded |
| `credit` | an ordinary transfer credits the owner; at most one credit program per account | none extra: the sender's one transfer fee |
| `execute` | `program_execute` transaction, sent by anyone, with `i13`..`i20` | the executor |

**No loops.** A payment made by a program (a leg, or a refund) never fires a credit trigger, and a program never creates or executes a program. One event causes at most one evaluation, and so at most one set of legs.

### 6. What the node checks: the same for every program

1. The trigger is enabled and the program is open and not expired.
2. It steps the rule **once, on a fresh engine** in the isolated payment-policy pool (#128 already runs payout rules this way, on the real engine). Install refuses clauses that reference other streams or earlier steps (`[t-k]`), so the result depends only on the inputs above. That makes it deterministic on every node, in apply and in replay (cf. #115).
3. The total of the legs is at most `i1` and at most `max_per_evaluation`.
4. Each debit from the owner's free balance goes through the owner's **current** o5 policy, fee step and balance check. These are the existing `_execute_fee_era_transfer` gates. A later freeze stops the program.
5. All legs or none. A failed evaluation leaves no debt, no partial payment and no advanced counter.

Install, replace and remove are rule changes of the account, judged by its rule-change guard (and by #33 when it exists), with `locked_until` as configuration. Program state lives in one book in `ConsensusLifecycleManager`: terms, escrow, counters and wake time. Its root joins the consensus meta hash only once it is used (no regenesis), it is restored with ids recomputed on load, and escrow counts in the supply check. Every term, plus `chain_id`, is in the signing preimage.

### 7. Every bounty item as a rule

All these rules were run on the real Tau engine, one fresh step each, and write exactly the amounts stated (17 cases in `tests/test_amendment_rules_native.py` on [my fork](https://github.com/nikifers85/tau-testnet/blob/design/one-rule-amendment/tests/test_amendment_rules_native.py)):

| Need | Configuration | The rule (abridged) |
|---|---|---|
| **Demo**: Alice sends Bob 5,000; up to 500 a week, from 1 Dec; the rest returns at the deadline | `fund` 5000, party 1 = Bob, triggers `execute`+`time`, period week | `o1 = i13` if `i5 ≥ 1 Dec ∧ i21 = 1 ∧ i13 ≤ i1 ∧ i2 + i13 ≤ 500`, else 0; `o9 = 1` if `i5 ≥ deadline` |
| Spending limits / automatic reset | period day, week, month or N seconds | `i2 + request ≤ cap` (the counter resets by period) |
| Time locks, days and hours | — | `i5 ≥ date`; `i7 < 5 ∧ 9 ≤ i8 < 17`; hold after arrival: `i6 ≥ N` |
| Refunds and expiry | `refund_to` | `o9 = 1` on a date, or when idle: `i6 ≥ 14 days` |
| #34 rent, 1,000 on the 31st (clamped), once a month | `fund` 0, party 1 = landlord, trigger `time`, period month | `o1 = 1000` if `i9 = i10 ∧ i3 = 0`; `o10 = 86400` |
| #35 20% of every incoming payment to savings | party 1 = savings, trigger `credit` | `o1 = i12 / 5` (floor; the owner keeps the rest) |
| #63 royalty 5% / 95% | parties artist and seller, trigger `execute` | `o1 = i13 / 20`, `o2 = i13 − i13 / 20` |
| Combined | — | any conjunction of the above. Conditions are AND-ed by the rule itself, and the rule text is the order of precedence |

A wallet shows exactly what applies to a payment: the program's rule and configuration, read from one RPC.

### 8. What changes against the posted design and #128

- **Removed:** the four feature-specific books and their 11 transaction types (grants, standing, routes, payouts). They become 3 types: `program_install` (also replaces), `program_execute`, `program_remove`.
- **Kept and reused:** integer period arithmetic, the isolated policy pool that already steps payout rules on the real engine, `chain_id` replay protection, the gated meta-hash root, persistence with tamper refusal, the escrow-in-supply check, the reorg tests and the two-node e2e harness.
- **#34 acceptance:** "at most once per period" is the rule's `i3 = 0`; the node guarantees the counters and atomicity. Insufficient funds: the evaluation fails and no counter moves. Cancellation is `program_remove`. Replays are prevented by the sequence and `chain_id`. Admission and apply call the same checks.
- **#35 acceptance:** the sender cannot bypass the program, because the split happens when the credit is applied. Routed money never triggers again. Gross, net and routed amounts are in the receipt.

### 9. How it will be proven

- Every acceptance case of #34 and #35, and every requirement of #63, through the real `apply_block`, with a mapping table like the one on #128.
- **The criterion as a test:** a combination with no example above (for example 300 a week, weekdays only, refund after 10 idle days, 10% royalty to a third party) is installed and runs with zero node changes. The same input map serves every rule.
- The example rules on the real engine (done), and the same rules end to end on two live nodes: proposer, restart, follower replay, same state hash.
- Determinism: the same program evaluated in apply, in replay from block 0 and on a follower gives the same legs.

### 10. Questions for the maintainers

1. Input and output map: are `i1`..`i21` / `o1`..`o10` above acceptable, or would you rather reuse the main spec's stream numbers?
2. Time-trigger fee: owner-paid per evaluation with a signed cap (above), or free within the per-block bound?
3. Should a program that debits the owner's free balance also be approved by the owner's o5 clause at install, or only at each payment (current proposal)?

I will start the build once you approve, or amend again if you want a different shape.
