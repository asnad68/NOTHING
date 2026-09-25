# Production Activation

## Objective

Bring the existing PostgreSQL billing path and receive-only XRPL worker to a
controlled production boundary without accidentally processing real payments.

## Current repository path

The authenticated-ingestion development line already contains:

- Payment Core and subscription billing
- PostgreSQL persistence
- XRP discovery/verification
- worker checkpoints
- payment allocation and entitlement activation
- production container CI

The PostgreSQL migration history is contiguous through schema version 13.
Migration **v6** is the per-invoice payment-routing migration.

## Safety gate

The XRPL worker now fails closed.

Default:

```
NOTHING_XRPL_ACTIVATION_MODE=disabled
```

Real processing requires all of:

```
NOTHING_XRPL_ACTIVATION_MODE=mainnet
NOTHING_XRPL_MAINNET_ACK=I_UNDERSTAND_REAL_XRPL_PAYMENTS
NOTHING_XRPL_ACCOUNT=<production receiving account>
NOTHING_XRPL_RPC_URL=<approved HTTPS XRPL endpoint>
NOTHING_POSTGRES_DSN=<production PostgreSQL DSN>
```

No private key or wallet seed is required by the receive-only worker.

## Deployment order

1. Build the API and worker images from a pinned commit.
2. Record the resulting image digests.
3. Inject production secrets outside Git.
4. Run the PostgreSQL migrator separately.
5. Confirm readiness reports the current schema version.
6. Start API containers.
7. Run synthetic payment/entitlement smoke tests.
8. Start the XRPL worker only after the mainnet activation gate has been reviewed.
9. Monitor worker heartbeat, payment allocation and entitlement audit events.

## Non-negotiable invariants

- A chain observation must match network, asset, destination and routing.
- Only successful finalized observations are eligible for automatic settlement.
- A chain event key is unique and cannot be allocated to two invoices.
- Invoice quote fields are immutable.
- Payment event identity fields are immutable.
- Entitlements are created at most once per invoice.
- Worker checkpoints cannot move backwards.
- Failed scans do not refresh the worker heartbeat.

## What is intentionally not done

This repository change does not execute a real-money transaction and does not
pretend that production infrastructure is already deployed. Hosting, secret
management and final mainnet approval remain deployment-level operations.

## Rollback

Keep the previous API/worker image digests available. Roll back the application
image first; do not manually mutate billing tables to "repair" a failed payment.
Use the reconciliation tooling and audit trail for payment exceptions.
