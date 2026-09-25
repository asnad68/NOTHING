# Payment Activation

## Current activation boundary

The payment path is implemented as a persistent, network-agnostic core:

```
invoice
  -> chain observation
  -> network / asset / destination / amount validation
  -> confirmation threshold
  -> payment state
  -> idempotent entitlement
```

XRP currently has a **synthetic test adapter only**. No private key, wallet seed,
mainnet listener, or real-money transaction is enabled by this change.

## Migration note

The repository currently contains storage migration v1. Payment Core is added as
migration v2 and Entitlement Core as v3 because migrations v2-v5 were not present
in the repository snapshot. Creating a fictional v6 while skipping missing
history would make production migration state untrustworthy.

Do not renumber these migrations until the missing historical migrations are
available and reconciled.

## Production gate

Before mainnet activation, all of the following must be true:

1. Full migration history is contiguous and verified.
2. Immutable API/worker images are built from a pinned commit and digest.
3. Production secrets are injected outside Git.
4. Real XRPL observations are independently validated for network, destination,
   delivered amount, transaction identity and finality.
5. Synthetic E2E tests pass, including duplicate replay.
6. A real-money canary is approved separately after legal and operational review.

This branch does not perform the final mainnet activation.
