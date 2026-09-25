# NOTHING Verify Web — Prototype

## Purpose

Verify Web is the human-facing read view of the NOTHING protocol graph.

It shows:

- Nothing ID
- subject
- recorded claim status
- current verification result
- verification scope
- evidence identifiers
- verification timeline
- exact procedure ID and version
- status synchronization mismatches

## Demo boundary

The current published dataset is synthetic.

The browser loads `site/data/demo-bundle.json`. No claim about a real third-party business is made by the demo.

This is intentional: the UI should prove that the interface can explain the protocol before real verification data is published.

## Architecture

The site is plain HTML, CSS and JavaScript.

There are no runtime framework dependencies and no external font, analytics or UI libraries.

The expected production migration is:

`Static Verify Web → GET /v1/identity/{nothing_id} → Resolver → Protocol data`

The browser should not become a second independent verification engine.

## Security considerations

The client escapes displayed text before inserting it into the DOM.

Future production work must additionally address:

- Content Security Policy
- strict transport security
- cache behavior
- API origin/CORS policy
- clickjacking protections
- dependency/supply-chain controls if dependencies are introduced

## GitHub Pages

The repository includes a custom GitHub Actions deployment workflow using the standard Pages deployment actions. GitHub documents custom workflow deployment through `configure-pages`, the Pages artifact upload action and `deploy-pages`.

For a project repository, the expected default Pages URL is:

`https://asnad68.github.io/NOTHING/`

The repository owner must ensure the Pages source is configured to use **GitHub Actions** in repository Settings → Pages. GitHub Pages supports static files and custom domains; server-side Python or PHP is not required for this prototype.

## Write boundary

Verify Web is intentionally read-only.

Business onboarding, evidence submission and verification-event publication occur through the authenticated ingestion API. The browser must never receive the ingestion bearer credential and must never call \`POST /v1/ingestion/bundles\`.

The production dependency remains:

\`Verify Web → GET /v1/identity/{nothing_id} → resolver-backed API\`

This keeps public verification separate from controlled data mutation.

