# Production payment operations

## Current live path

The repository contains a live XRPL discovery worker for the supplied treasury address:

```
r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2
```

The worker only considers validated XRP Payment transactions and uses the
transaction metadata field `delivered_amount` for the actual amount delivered.
This avoids trusting the requested amount on a partial payment.

The worker discovers payments, matches the XRP destination tag to an invoice,
and delegates settlement to the PostgreSQL billing service. The service then
performs payment allocation and entitlement activation atomically.

The worker uses a durable PostgreSQL checkpoint and paginates the XRPL
`account_tx` result with markers. It advances the checkpoint only after every
discovered observation in the scan has been processed successfully. A trusted
payment that has no matching invoice is persisted as an unmatched payment event
and audited rather than silently discarded.

XRPL's official guidance recommends recording the latest processed transaction
and ledger, checking `tesSUCCESS`, using validated results as final, and handling
partial payments via `delivered_amount`.

## Required production configuration

Set:

- `NOTHING_POSTGRES_DSN` in `nothing-payment-worker-secrets`
- `NOTHING_XRPL_RPC_URL` to a trusted HTTPS XRPL JSON-RPC endpoint
- `NOTHING_XRPL_ACCOUNT` to the receiving XRPL address
- `NOTHING_PAYMENT_ACTOR` to an operator identity
- `NOTHING_PAYMENT_POLL_SECONDS` to the desired polling interval

The repository default uses the current XRPL public JSON-RPC endpoint
`https://xrplcluster.com/` for read-only observation. The public server is a
starting point, not a substitute for operational redundancy or provider-level
SLA planning. XRPL publishes public servers for read access and recommends
running your own server when you need full control.

## Billing API

The authenticated Billing API provides:

- `POST /v1/billing/invoices` for creating customer-scoped invoices
- `GET /v1/billing/invoices/{invoice_id}` for current invoice state
- `GET /v1/billing/entitlements` for active customer entitlements

Billing uses a dedicated `nothing:billing` permission. The client cannot supply
the customer reference, treasury destination or XRP routing tag. Those values
are derived or allocated server-side.

The API returns both the exact integer `amount_atomic` and a display amount.
Do not use floating-point arithmetic for settlement or entitlement decisions.

## XRP invoice routing

The XRP treasury address is shared, so each automatic invoice must have a unique
non-zero uint32 destination tag. The billing service allocates a tag with a
database collision check.

The tag is part of the displayed payment instructions. A customer should send
XRP to the treasury address and include the invoice's destination tag.

Payments without a tag, or with a tag that does not map to an open invoice, go
to review/manual handling and never activate an entitlement automatically.

## Reconciliation

Do not consider the worker's last successful poll a final accounting checkpoint.
The database is authoritative for allocation and entitlement state.

At least once per operational interval:

1. verify worker health/log heartbeat
2. check recent XRPL scan errors
3. inspect review_required invoices
4. reconcile received payment events against invoices
5. investigate orphaned or contradictory observations
6. verify PostgreSQL backups and audit retention

## ETH and BTC

The live verifier adapters for Ethereum and Bitcoin are present, but automatic
discovery is intentionally not enabled for the currently supplied shared
addresses.

ETH requires an invoice-specific destination and a configured JSON-RPC endpoint
with an explicit expected chain ID.

BTC requires an invoice-specific destination and a configured Bitcoin Core RPC
endpoint with appropriate transaction lookup/wallet indexing.

Do not enable automatic ETH/BTC settlement against the supplied shared treasury
addresses.

## Key handling

This payment path is receive-only.

No private key, seed phrase or signing operation is needed by the worker.
Never give the worker a wallet secret merely to observe incoming payments.
