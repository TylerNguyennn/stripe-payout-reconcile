import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import reconcile  # noqa: E402

SAMPLE = ROOT / "sample"


class SampleTest(unittest.TestCase):
    def setUp(self):
        self.payouts = reconcile.load_payouts(SAMPLE / "stripe_itemized.csv")
        self.deposits = reconcile.load_deposits(SAMPLE / "bank.csv")
        self.matches = reconcile.match(self.payouts, self.deposits)

    def test_unpaid_balance_transactions_are_skipped(self):
        self.assertEqual(list(self.payouts), ["po_1A", "po_1B", "po_1C", "po_1D", "po_1E"])

    def test_every_journal_entry_balances(self):
        for p in self.payouts.values():
            rows = reconcile.journal_rows(p)
            debits = sum(Decimal(r[3] or "0") for r in rows)
            credits = sum(Decimal(r[4] or "0") for r in rows)
            self.assertEqual(debits, credits, p.payout_id)

    def test_dispute_fee_lands_in_fees_not_chargebacks(self):
        b = self.payouts["po_1B"].by_account
        self.assertEqual(b["Chargebacks"], Decimal("-120.00"))
        self.assertEqual(b["Stripe fees"], Decimal("-29.85"))

    def test_matching(self):
        self.assertEqual(self.matches["po_1A"].row, 3)
        self.assertEqual(self.matches["po_1B"].row, 5)
        self.assertEqual(self.matches["po_1C"].row, 6)
        self.assertIsNone(self.matches["po_1D"])
        self.assertIsNone(self.matches["po_1E"])

    def test_report_explains_each_problem_once(self):
        report = reconcile.render_report(self.payouts, self.matches, self.deposits)
        self.assertIn("off by -25.00", report)
        self.assertIn("po_1E** (57.46): no deposit", report)
        self.assertIn("climate_order", report)
        self.assertIn("Bank row 2", report)
        self.assertNotIn("Bank row 7", report)


if __name__ == "__main__":
    unittest.main()
