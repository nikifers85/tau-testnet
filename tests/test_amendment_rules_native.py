"""Bounty 01 amendment (IDNI#34): every example rule of the one-rule design runs
on the REAL Tau engine and writes the amounts the amendment states.

Each rule is stepped once on a fresh engine (one evaluation = one fresh step),
with the fixed input map of the amendment. Skipped when the native tau module
is not built.
"""
import pytest

import tau_policy
from consensus.payouts import tau_amount
from consensus.sender_policy import clause_inputs


def _native_available():
    try:
        import tau_native
        tau_native.load_tau_module()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _native_available(), reason="native tau module not built")

DEC1, DEADLINE = 1795996800, 1798675200   # 2026-12-01, 2027-01-01 UTC


def A(n):
    return f"i{n}[t]:bv[24]"


def T(n):
    return f"i{n}[t]:bv[64]"


def c(v, w=24):
    return "{ %d }:bv[%d]" % (v, w)


def o(n, w=24):
    return f"o{n}[t]:bv[{w}]"


def _if(cond, out, value, zero=None):
    zero = zero or c(0)
    return f"(({cond}) -> {out} = {value}) && (!({cond}) -> {out} = {zero})"


GRANT_DRAW = (f"{T(5)} >= {c(DEC1, 64)} && {A(21)} = {c(1)} && {A(13)} <= {A(1)} "
              f"&& {A(2)} + {A(13)} <= {c(500)}")
WINDOW = f"{A(7)} < {c(5)} && {A(8)} >= {c(9)} && {A(8)} < {c(17)} && {A(13)} <= {c(100)}"

RULES = {
    # Alice -> Bob 5,000 in escrow: from 1 Dec, at most 500 per week, drawn by
    # Bob (party 1); what is left returns to Alice at the deadline.
    "grant": (_if(GRANT_DRAW, o(1), A(13)) + " && "
              + _if(f"{T(5)} >= {c(DEADLINE, 64)}", o(9), c(1)) + " && "
              + f"{o(10, 64)} = {c(86400, 64)}"),
    # Rent: 1,000 on the last day of every month (the 31st clamps), once a period.
    "rent": (_if(f"{A(9)} = {A(10)} && {A(3)} = {c(0)}", o(1), c(1000))
             + f" && {o(10, 64)} = {c(86400, 64)}"),
    # Receive route: 20% of every incoming credit, rounded down.
    "route": f"{o(1)} = {A(12)} / {c(5)}",
    # Royalty: 5% to the artist, the rest to the seller.
    "royalty": f"{o(1)} = {A(13)} / {c(20)} && {o(2)} = {A(13)} - {A(13)} / {c(20)}",
    # Use it or lose it: close and refund after 14 days without a payment.
    "idle": _if(f"{T(6)} >= {c(14 * 86400, 64)}", o(9), c(1)),
    # Combined: weekdays 9-17 UTC only, at most 100 a payment.
    "window": _if(WINDOW, o(1), A(13)),
}


def _inputs(**kw):
    values = {n: 0 for n in range(1, 22)}
    values.update({9: 1, 10: 31, 11: 3})
    values.update({int(k[1:]): v for k, v in kw.items()})
    return clause_inputs({k: str(v) for k, v in values.items()})


CASES = [
    ("grant", dict(i1=5000, i5=DEC1 - 3600, i13=100, i21=1), {"o1": 0, "o9": 0}),   # time lock
    ("grant", dict(i1=5000, i5=DEC1 + 60, i13=300, i21=1), {"o1": 300, "o9": 0}),
    ("grant", dict(i1=4700, i2=300, i5=DEC1 + 86400, i13=201, i21=1), {"o1": 0}),   # over the cap
    ("grant", dict(i1=4700, i2=300, i5=DEC1 + 86400, i13=200, i21=1), {"o1": 200}),
    ("grant", dict(i1=4500, i5=DEC1 + 86400, i13=100, i21=2), {"o1": 0}),          # not Bob
    ("grant", dict(i1=4500, i5=DEADLINE, i11=1), {"o1": 0, "o9": 1}),                # refund
    ("rent", dict(i9=30, i10=30), {"o1": 1000}),                                     # 30-day month
    ("rent", dict(i9=30, i10=31), {"o1": 0}),
    ("rent", dict(i9=28, i10=28, i3=1), {"o1": 0}),                                  # already paid
    ("route", dict(i12=101), {"o1": 20}),
    ("royalty", dict(i13=1000), {"o1": 50, "o2": 950}),
    ("idle", dict(i6=14 * 86400), {"o9": 1}),
    ("idle", dict(i6=13 * 86400), {"o9": 0}),
    ("window", dict(i7=2, i8=10, i13=100), {"o1": 100}),
    ("window", dict(i7=5, i8=10, i13=100), {"o1": 0}),                               # Saturday
    ("window", dict(i7=2, i8=18, i13=50), {"o1": 0}),                                # after 17
    ("window", dict(i7=2, i8=10, i13=101), {"o1": 0}),                               # over 100
]


@pytest.mark.parametrize("name,inputs,want", CASES)
def test_example_rule_on_real_tau(name, inputs, want):
    ev = tau_policy.PolicyEvaluator(pool="payout")   # a fresh engine per evaluation
    try:
        outs = ev.outputs(RULES[name], _inputs(**inputs), timeout=300)
    finally:
        ev.close()
    assert {k: tau_amount(outs.get(k)) for k in want} == want
