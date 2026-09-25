[object Object]

## Routing rule for automated subscriptions

The addresses in payment_addresses.json are treasury/shared receiving addresses. They are not sufficient for safe automatic invoice attribution when multiple invoices may exist concurrently.

Automatic entitlement activation requires an invoice-specific settlement route:

- a unique receiving address for the invoice, or
- for XRPL, a unique destination tag together with the treasury address

A shared treasury address remains available for manual payment review.

The payment settlement service therefore fails closed to review_required when an invoice has manual_shared routing.
