# NOTHING Payment to Subscription Architecture

## Settlement pipeline

The production payment boundary is:

Invoice -> Trusted chain adapter/indexer -> Payment observation -> Exact asset/network/destination matching -> Finality policy -> Payment allocation -> Subscription entitlement

A payer must never be able to submit confirmation, amount, destination, asset contract or finality as authoritative facts.

## Invoice contract

Every invoice snapshots the exact settlement tuple:

- customer reference
- plan code
- price identifier
- network
- asset code
- asset kind
- token contract when applicable
- destination
- integer atomic amount
- asset decimals
- expiration
- client idempotency key

Settlement uses integer atomic units. Floating-point values are not used for money comparison.

No price is invented by the payment processor. The invoice is created from an active operator-defined billing price, and the quote is then frozen.

## Asset and network detection

The checkout fixes the network and asset before payment.

A chain adapter independently verifies the transaction against that tuple.

Ethereum native payment:
- correct chain
- successful receipt
- exact destination
- exact native value
- configured finality policy

ERC-20:
- all native checks where relevant
- exact token contract
- successful receipt
- Transfer event
- exact recipient
- exact token amount
- log index as part of the duplicate key

Bitcoin:
- verify the exact UTXO/output
- match destination
- match amount
- use transaction hash plus output index as the unique chain event key
- track confirmation depth and reorganization state

XRP Ledger:
- use a validated ledger result
- require successful transaction result
- match destination
- include destination tag in the invoice tuple when a deployment uses destination tags

XRPL documentation states that only transactions in validated ledgers have immutable final results. Ethereum exposes transaction receipts and safe/finalized block references through its JSON-RPC interfaces.

## Confirmation policy

Confirmation policy is configuration, not a client input.

Each network receives a policy containing:

- required confirmation count when count-based settlement is used
- whether explicit finality is required
- maximum observation age
- reorganization handling

No global confirmation number is hard-coded because the risk tolerance and network semantics differ.

## Duplicate protection

There are three levels:

1. Invoice creation idempotency
   customer reference plus client idempotency key is unique.

2. Chain event idempotency
   network plus chain event key is unique.

3. Entitlement idempotency
   invoice identifier is unique in the entitlement table.

Examples of chain event keys:

- Ethereum native: network + transaction hash
- ERC-20: network + transaction hash + log index
- Bitcoin: network + transaction hash + output index
- XRP Payment: network + transaction hash

The same payment event cannot be allocated to two invoices.

## Partial payments

A finalized payment below the invoice amount is recorded as an allocation but does not activate an entitlement.

Additional finalized payments can be allocated to the same invoice until the required amount is reached.

The settlement decision is based on the sum of immutable payment allocations, not only the latest observation.

## Overpayment

If received value exceeds the invoice amount:

- invoice status becomes overpaid
- the purchased entitlement remains exactly the plan duration
- the excess amount is retained as recorded accounting state
- no automatic refund is performed

A future refund process must be explicit and separately authorized.

## Expired invoice

A payment observed after invoice expiry never silently grants access.

It is marked review_required and requires an explicit business decision.

## Atomic entitlement activation

The final payment allocation and entitlement activation happen in the same PostgreSQL transaction.

Either both commit, or neither commits.

The entitlement table has a unique invoice identifier, so retrying the same settlement cannot create a second entitlement.

For renewals, the new entitlement starts at the later of:
- the current time
- the existing active entitlement expiry for the same customer and plan

This prevents paid renewals from unintentionally shortening or overlapping the existing service period.

## Reorganizations and finality changes

Before finality, an observation may move through:

pending -> confirmed -> final

or:

pending/confirmed -> orphaned

An orphaned observation is never allocated.

A finalized event must not be silently changed to orphaned by an ordinary observation update. Such a contradiction is a conflict and requires investigation.

The database also protects payment identity fields, invoice quote fields and entitlement identity fields with triggers.

## Trusted adapter boundary

The future chain adapters are responsible only for producing trusted observations.

The adapter output must include:

- network
- asset
- asset kind
- token contract when applicable
- destination
- amount in atomic units
- transaction hash
- chain event key
- block or ledger reference
- confirmation information
- finality status
- success result
- observation source

The browser, wallet callback, customer and public API must never be trusted as the source of these facts.

## Current assets

Enabled receiving entries currently are:

- XRP on XRPL
- ETH on Ethereum
- BTC on Bitcoin

USDT remains disabled because the supplied hexadecimal receiving address does not determine the settlement network. Enabling USDT requires an explicit network and the exact token contract in the payment configuration.

## Current implementation

Repository work now includes:

- payment domain primitives
- exact integer amount matching
- asset/network/destination/contract matching
- configurable finality classification
- PostgreSQL invoice, payment event, allocation and entitlement schema
- invoice request idempotency
- blockchain event uniqueness
- immutable allocation records
- database guards for immutable payment identities
- atomic settlement service
- unit tests for payment classification
- PostgreSQL integration coverage for one-time entitlement activation

Still intentionally separate from this phase:

- live chain RPC/indexer clients
- webhook infrastructure
- customer-facing billing API
- fiat exchange-rate oracle
- refund automation

These components must enter through the trusted observation boundary and must not bypass PostgreSQL settlement invariants.

## Security boundary

No blockchain private keys, seed phrases, signing credentials or exchange secrets belong in the API container or repository.

This phase is receive-only. It detects and verifies incoming payments; it does not sign or send funds.
