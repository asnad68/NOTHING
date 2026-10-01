# NOTHING Browser Extension

Cross-browser MV3 extension for the NOTHING business identity and verification project.

## What it does

1. Inspects the active page only after a user click.
2. Pre-fills a business/brand registration draft from page title, canonical URL, hostname and selected public metadata.
3. Stores the draft locally in browser storage.
4. Opens the deployed NOTHING registration web page.
5. Connects a compatible EIP-1193/EIP-6963 wallet on the registration page.
6. Opens public verification records by NOTHING ID.

The extension is intentionally **not a wallet**. MetaMask, Trust Wallet or another compatible wallet remains the user's wallet. The extension never receives a seed phrase or private key.

## Paid enrollment

The production flow is:

registration draft -> server-issued invoice -> wallet transaction -> server-side chain verification -> Payment Core settlement -> NOTHING ID -> public Verify page.

A personal EVM wallet can be the receiving address. The invoice carries a unique routing reference. The wallet transaction includes the UTF-8 payload `NOTHING|<routing_reference>` in EVM calldata, and the server verifies it before settling the invoice. Amount alone is never used as the identity of a payment.

## Identity and hologram semantics

Payment does not equal independent verification. A successful enrollment creates a NOTHING identity record whose submitted claims are marked `SELF-CLAIMED` until independent verification events are recorded.

The first badge is an SVG web marker linking to `verify.html?id=NTH-XXXXXX`. It is not an on-chain NFT. A future NFT implementation requires a real minting adapter and recorded token identifier.

## Security boundary

The extension requests only `activeTab`, `scripting` and `storage`. It does not request browsing history, cookies, passwords, seed phrases or private keys.

## Build

```bash
python extension/build.py all
```

## Store publication

The repository contains the source and packaging workflow. Official store publication still requires the owner's browser-store developer account, required verification/2FA, listing metadata and submission.
