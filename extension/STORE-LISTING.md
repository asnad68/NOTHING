# NOTHING — Business Identity (store listing draft)

## One-line description

Inspect a business identity, prepare a registration draft, and open a public NOTHING verification record from your browser.

## Full description

NOTHING is an experimental business identity and verification project.

The extension provides a focused workflow:

1. Inspect the active page only after the user clicks **Inspect current page**.
2. Capture page title, URL, hostname and selected public metadata.
3. Edit the business/brand record before submitting it.
4. Open NOTHING registration.
5. Connect a compatible wallet on the registration web page when the service is live.
6. Open public verification records by Nothing ID.

The extension is not a crypto wallet. It never asks for a seed phrase or private key.

A NOTHING identity record is a project-level reference for claims and evidence. It is not a government registration, trademark registration, certificate, banking credential or legal certification.

Paid registration, where enabled, is handled by the deployed NOTHING service. The service creates the payment quote and verifies the resulting blockchain transaction server-side before granting the registration entitlement.

The clickable NOTHING badge is a web identity marker. A future NFT implementation would require a real on-chain minting adapter and a recorded token identifier; the current badge must not be described as an NFT.

## Permission explanation

- `activeTab`: inspect the current tab after an explicit user action.
- `scripting`: read the limited page metadata needed for the registration draft.
- `storage`: keep the draft locally in browser storage.

No browsing-history or cookies permission is requested.

## Privacy

See the public privacy page included in the repository: `site/privacy.html`.

## Independence

NOTHING is an independent project and is not affiliated with Nothing Technology Limited.
