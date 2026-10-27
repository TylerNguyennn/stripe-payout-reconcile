# stripe-payout-reconcile

Stripe deposits one number into your bank. Your books need five: sales, refunds, chargebacks, Stripe fees and adjustments. When they don't tie out, someone spends an afternoon in spreadsheets.

This script takes Stripe's **itemized payout reconciliation report** and your **bank statement**, then:

1. splits every payout into journal lines that balance to the exact bank deposit (ready to import into QuickBooks or Xero),
2. matches each payout to its deposit (exact amount, ±3 days),
3. explains what doesn't match: a deposit that is off by a fee booked elsewhere, a payout still in transit, a Stripe deposit with no payout in the report, or a Stripe category nobody mapped yet.

No dependencies, no API keys. Python 3.9+.

## Run it on the sample

```bash
python3 reconcile.py sample/stripe_itemized.csv sample/bank.csv --out out
```

```
5 payouts, 3 matched to a bank deposit, 2 need attention.

| Payout | Date       |    Net |   Sales | Refunds | Chargebacks |   Fees | Adjustments | Bank                  |
|--------|------------|-------:|--------:|--------:|------------:|-------:|------------:|-----------------------|
| po_1A  | 2026-10-01 | 299.25 |  350.00 |  -40.00 |        0.00 | -10.75 |        0.00 | ✅ row 3 (2026-10-02) |
| po_1B  | 2026-10-03 | 350.15 |  500.00 |    0.00 |     -120.00 | -29.85 |        0.00 | ✅ row 5 (2026-10-06) |
| po_1C  | 2026-10-05 | 197.38 |   80.00 |    0.00 |      120.00 |  -2.62 |        0.00 | ✅ row 6 (2026-10-06) |
| po_1D  | 2026-10-07 | 870.70 | 1000.00 | -100.00 |        0.00 | -29.30 |        0.00 | ❌ no match           |
| po_1E  | 2026-10-09 |  57.46 |   60.00 |    0.00 |        0.00 |  -2.04 |       -0.50 | ❌ no match           |

## Needs attention
- po_1D (870.70): closest deposit is row 7 (845.70 on 2026-10-08), off by -25.00. Check for a fee or refund booked outside Stripe.
- po_1E (57.46): no deposit within 3 days. In transit, failed, or paid to another account?
- po_1E: unmapped categories ['climate_order'] were booked to Stripe adjustments. Review before posting.
- Bank row 2: Stripe deposit 412.00 on 2026-09-29 has no payout in this report. Wrong date range?
```

`out/journal.csv` holds one balanced journal entry per payout:

```
JournalNo,JournalDate,Account,Debits,Credits,Description
po_1B,2026-10-03,Bank,350.15,,Stripe payout po_1B
po_1B,2026-10-03,Chargebacks,120.00,,Stripe payout po_1B
po_1B,2026-10-03,Sales,,500.00,Stripe payout po_1B
po_1B,2026-10-03,Stripe fees,29.85,,Stripe payout po_1B
```

The sample data is fake but covers the cases that usually break the books: partial refunds, a chargeback with its $15 dispute fee, a won dispute coming back in a later payout, standalone Radar fees, a Stripe Climate deduction, and a balance transaction that isn't paid out yet.

## Inputs

**Stripe:** Dashboard → Reports → Payout reconciliation → Download → *Itemized*. Uses `automatic_payout_id`, `automatic_payout_effective_at`, `currency`, `gross`, `fee`, `net`, `reporting_category`.

**Bank:** any CSV with `date`, `amount`, `description`. Debits (negative amounts) are ignored.

Account names live in `ACCOUNTS` at the top of `reconcile.py`; rename them to match your chart of accounts.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Part of a small finance-ops toolkit

- [stripe-payout-reconcile](https://github.com/TylerNguyennn/stripe-payout-reconcile): split Stripe payouts into journal lines and match them to the bank statement.
- [invoice-intake](https://github.com/TylerNguyennn/invoice-intake): supplier invoices from a mailbox, read by Claude, checked, approved, exported to Xero or QuickBooks.
- [finops-mcp](https://github.com/TylerNguyennn/finops-mcp): ask Claude about receivables, payouts, disputes, bills and cash, read-only over Stripe.

## Need it for your setup?

Real setups add more: several Stripe accounts, PayPal or Shopify Payments next to Stripe, multi-currency payouts, or pushing the entries straight into QuickBooks/Xero via API. I build and fix payment integrations for a living (PSPs, webhooks, payouts, reconciliation). Get in touch through [taewoo.me](https://taewoo.me).

## License

MIT
