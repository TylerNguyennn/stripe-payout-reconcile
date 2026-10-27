"""Reconcile Stripe payouts against bank deposits and build journal entries.

Input 1: Stripe "Payout reconciliation - itemized" report (Dashboard > Reports >
         Payout reconciliation > Download itemized). One row per balance transaction.
Input 2: bank statement / QuickBooks bank feed CSV with columns date, amount, description.

Output: a Markdown report (what matched, what didn't, why) and a journal CSV that
splits every payout into sales, refunds, disputes, fees and adjustments, so the
single bank deposit ties out in QuickBooks or Xero.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

# Stripe reporting_category -> journal account. Anything not listed lands in
# "Stripe adjustments" and is called out in the report so nobody books it blind.
ACCOUNTS = {
    "charge": "Sales",
    "refund": "Refunds",
    "refund_failure": "Refunds",
    "dispute": "Chargebacks",
    "dispute_reversal": "Chargebacks",
    "fee": "Stripe fees",
    "other_adjustment": "Stripe adjustments",
}
FEE_ACCOUNT = "Stripe fees"
DATE_WINDOW_DAYS = 3


@dataclass
class Payout:
    payout_id: str
    currency: str
    effective: date
    lines: List[dict] = field(default_factory=list)
    by_account: Dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    unknown_categories: set = field(default_factory=set)

    @property
    def net(self) -> Decimal:
        return sum((Decimal(l["net"]) for l in self.lines), Decimal("0"))


@dataclass
class Deposit:
    row: int
    when: date
    amount: Decimal
    description: str
    matched: Optional[str] = None


def parse_date(text: str) -> date:
    text = text.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date: {text!r}")


def money(text: str) -> Decimal:
    return Decimal(text.replace(",", "").replace("$", "").strip() or "0")


def load_payouts(path: Path) -> "OrderedDict[str, Payout]":
    payouts: "OrderedDict[str, Payout]" = OrderedDict()
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            pid = row.get("automatic_payout_id", "").strip()
            if not pid:
                # Balance transactions not yet paid out belong to a future payout.
                continue
            p = payouts.get(pid)
            if p is None:
                p = payouts[pid] = Payout(pid, row["currency"].lower(), parse_date(row["automatic_payout_effective_at"]))
            p.lines.append(row)
            category = row["reporting_category"].strip()
            account = ACCOUNTS.get(category)
            if account is None:
                account = "Stripe adjustments"
                p.unknown_categories.add(category)
            gross, fee = money(row["gross"]), money(row["fee"])
            if category == "fee":
                # Standalone fees (Radar, billing, etc.) report the cost in gross.
                p.by_account[FEE_ACCOUNT] += gross - fee
            else:
                p.by_account[account] += gross
                p.by_account[FEE_ACCOUNT] -= fee
    return payouts


def load_deposits(path: Path) -> List[Deposit]:
    deposits = []
    with path.open(newline="") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            amount = money(row["amount"])
            if amount <= 0:
                continue
            deposits.append(Deposit(i, parse_date(row["date"]), amount, row.get("description", "")))
    return deposits


def match(payouts: "OrderedDict[str, Payout]", deposits: List[Deposit]) -> Dict[str, Optional[Deposit]]:
    """Exact amount within the date window; closest date wins."""
    result: Dict[str, Optional[Deposit]] = {}
    for p in payouts.values():
        candidates = [
            d for d in deposits
            if d.matched is None and d.amount == p.net and abs((d.when - p.effective).days) <= DATE_WINDOW_DAYS
        ]
        candidates.sort(key=lambda d: abs((d.when - p.effective).days))
        hit = candidates[0] if candidates else None
        if hit:
            hit.matched = p.payout_id
        result[p.payout_id] = hit
    return result


def near_miss(p: Payout, deposits: List[Deposit]) -> Optional[Deposit]:
    """Closest unmatched deposit in the window that is plausibly this payout
    (within 10% or 50.00), used to explain a mismatch. Claims the deposit."""
    tolerance = max(p.net * Decimal("0.10"), Decimal("50"))
    pool = [
        d for d in deposits
        if d.matched is None and abs((d.when - p.effective).days) <= DATE_WINDOW_DAYS
        and abs(d.amount - p.net) <= tolerance
    ]
    hit = min(pool, key=lambda d: abs(d.amount - p.net)) if pool else None
    if hit:
        hit.matched = f"~{p.payout_id}"
    return hit


def journal_rows(p: Payout) -> List[List[str]]:
    """Debit bank/fees/refunds, credit sales, so the entry nets to the payout."""
    rows = []
    memo = f"Stripe payout {p.payout_id}"
    rows.append([p.payout_id, p.effective.isoformat(), "Bank", f"{p.net:.2f}", "", memo])
    for account, amount in sorted(p.by_account.items()):
        if amount == 0:
            continue
        debit, credit = ("", f"{amount:.2f}") if amount > 0 else (f"{-amount:.2f}", "")
        rows.append([p.payout_id, p.effective.isoformat(), account, debit, credit, memo])
    return rows


def render_report(payouts, matches, deposits) -> str:
    out = ["# Stripe payout reconciliation", ""]
    ok = sum(1 for d in matches.values() if d)
    out.append(f"{len(payouts)} payouts, {ok} matched to a bank deposit, {len(payouts) - ok} need attention.")
    out.append("")
    out.append("| Payout | Date | Net | Sales | Refunds | Chargebacks | Fees | Adjustments | Bank |")
    out.append("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for p in payouts.values():
        a = p.by_account
        d = matches[p.payout_id]
        bank = f"✅ row {d.row} ({d.when})" if d else "❌ no match"
        out.append(
            f"| {p.payout_id} | {p.effective} | {p.net:.2f} | {a['Sales']:.2f} | {a['Refunds']:.2f} | "
            f"{a['Chargebacks']:.2f} | {a[FEE_ACCOUNT]:.2f} | {a['Stripe adjustments']:.2f} | {bank} |"
        )
    problems = []
    for p in payouts.values():
        if matches[p.payout_id] is None:
            miss = near_miss(p, deposits)
            if miss:
                diff = miss.amount - p.net
                problems.append(
                    f"- **{p.payout_id}** ({p.net:.2f}): closest deposit is row {miss.row} "
                    f"({miss.amount:.2f} on {miss.when}), off by {diff:+.2f}. Check for a fee or refund booked outside Stripe."
                )
            else:
                problems.append(f"- **{p.payout_id}** ({p.net:.2f}): no deposit within {DATE_WINDOW_DAYS} days. In transit, failed, or paid to another account?")
        if p.unknown_categories:
            problems.append(f"- **{p.payout_id}**: unmapped categories {sorted(p.unknown_categories)} were booked to Stripe adjustments. Review before posting.")
    stray = [d for d in deposits if d.matched is None and "stripe" in d.description.lower()]
    for d in stray:
        problems.append(f"- Bank row {d.row}: Stripe deposit {d.amount:.2f} on {d.when} has no payout in this report. Wrong date range?")
    out += ["", "## Needs attention", ""] + (problems or ["Nothing. Every payout ties to a deposit."])
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("itemized", type=Path, help="Stripe payout reconciliation itemized CSV")
    ap.add_argument("bank", type=Path, help="bank statement CSV (date, amount, description)")
    ap.add_argument("--out", type=Path, default=Path("out"), help="output folder (default: out)")
    args = ap.parse_args(argv)

    payouts = load_payouts(args.itemized)
    deposits = load_deposits(args.bank)
    matches = match(payouts, deposits)

    args.out.mkdir(parents=True, exist_ok=True)
    report = render_report(payouts, matches, deposits)
    (args.out / "report.md").write_text(report)
    with (args.out / "journal.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["JournalNo", "JournalDate", "Account", "Debits", "Credits", "Description"])
        for p in payouts.values():
            w.writerows(journal_rows(p))
    print(report)
    return 0 if all(matches.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
