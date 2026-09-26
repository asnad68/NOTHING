# Payments — Deferred Phase Lock

Payment processing is intentionally frozen while the identity and verification core is developed.

## Current state

- No new payment features are being developed in this phase.
- No card or bank checkout is exposed by the identity prototype.
- Existing payment code is retained in the repository but is outside the current implementation scope.
- Real-money activation is a future release gate.

## Future gate

Cryptocurrency settlement will be revisited only after:

1. business/legal structure is established;
2. applicable jurisdiction and AML/CFT obligations are mapped;
3. asset and network configuration is reviewed;
4. custody and key-management responsibilities are defined;
5. reconciliation, refunds, disputes and accounting are defined;
6. security testing and operational monitoring are ready.

Until those gates are satisfied, the project must not present payment functionality as live production infrastructure.
