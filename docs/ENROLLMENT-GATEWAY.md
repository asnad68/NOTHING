# NOTHING Public Enrollment Gateway

The browser registration flow is deliberately separated from the authenticated core API.

## Flow

Browser extension / registration page -> wallet connection -> server-created invoice -> wallet transaction -> server-side chain verification -> Payment Core settlement -> identity ingestion -> public Verify page.

The gateway never asks for a seed phrase or private key and never trusts a browser-reported payment as proof.

## Production prerequisites

The gateway must run beside the existing PostgreSQL-backed NOTHING services.

Required environment:

- `NOTHING_ENROLLMENT_ENABLED=true`
- `NOTHING_POSTGRES_DSN=<production DSN>`
- `NOTHING_ENROLLMENT_PRICE_ID=<UUID of a native ETH price>`
- `NOTHING_ENROLLMENT_EVM_RPC_URL=https://<trusted-rpc>`
- `NOTHING_ENROLLMENT_EVM_NETWORK=ethereum`
- `NOTHING_ENROLLMENT_EVM_CHAIN_ID=1`
- `NOTHING_VERIFY_BASE_URL=https://<public-nothing-site>`
- `NOTHING_ENROLLMENT_ALLOWED_ORIGIN=https://<public-nothing-site>`

The configured billing price must use:

- asset: ETH
- network: ethereum
- asset kind: native
- routing mode: unique_destination
- an invoice-specific destination

Do not configure a shared treasury address for automatic enrollment settlement.

## Start

From repository root:

```bash
python -m src.nothing_enrollment_gateway
```

Place the gateway behind TLS. The public registration site should set `window.NOTHING_ENROLLMENT_API_BASE` to the gateway's HTTPS origin.

## ID semantics

Nothing IDs are stable six-digit project identifiers such as `NTH-000101`. Allocation is deterministic from the normalized registration digest with collision candidates. The ID is not a trademark number, government registration, legal certificate or independent verification result.

## Hologram / badge

The first release uses an SVG web badge that links to `verify.html?id=NTH-XXXXXX`. It is a clickable public identity marker. It is **not an NFT** until a future on-chain minting adapter is implemented and its token identifier is recorded in the NOTHING proof/identity model.

## Payment pricing

A price such as USD 10 must be defined by the operator in the billing catalog and, if the settlement asset is ETH, converted into a frozen integer ETH amount before invoice creation. Do not put a floating USD calculation in the browser. The browser only displays and pays the server-issued quote.
