# Crypto payment boundary

The repository now contains the public receiving-address registry at:
`config/payment_addresses.json`

## Supported receiving assets

| Asset | Network | Address | Status |
|---|---|---|---|
| XRP | XRPL | `r9LCAZDtwe8qeCv5X3BtD9ziBeqENLzCy2` | Enabled for manual receiving |
| ETH | Ethereum | `0xE1c90171271B5325beE02592ACc50A510448d03E` | Enabled for manual receiving |
| BTC | Bitcoin | `3ABUrDAmi6w9TRsHuwFgdcDBLvXUzbAfZY` | Enabled for manual receiving |
| USDT | **Not specified** | `0xE1c90171271B5325beE02592ACc50A510448d03E` | **Disabled until the exact network is declared** |

The ETH and USDT addresses share the same hexadecimal address string, but that does not by itself identify the USDT settlement network. The checkout layer must require an explicit USDT network before displaying or accepting payment instructions.

## Current payment boundary

The addresses are public receiving identifiers, not credentials.

Never place any of the following in Git:

- private keys
- seed phrases
- wallet passwords
- signing secrets
- exchange API secrets

The current project does **not** automatically confirm a subscription from a blockchain transaction. A future payment implementation must bind each payment to an invoice/order identifier and verify the correct asset, network, amount, destination and confirmation state before granting subscription access.

Before enabling automated settlement, define:

1. subscription price and currency
2. invoice identifier
3. exact chain/network for each supported asset
4. confirmation policy
5. underpayment/overpayment policy
6. duplicate transaction handling
7. refund policy
8. exchange-rate policy, if prices are denominated in fiat
9. webhook/indexer security model
10. audit trail for payment-to-subscription entitlement

Until those controls exist, the safe mode is manual verification.
