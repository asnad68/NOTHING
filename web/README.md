# NOTHING Verify Web

This is a deployment-neutral static verifier for the public NOTHING API.

## Runtime

The page expects the API origin to be the same origin by default.

For a separate static host, define a bootstrap value before loading app.js:

```html
<script>
  window.NOTHING_API_BASE = "https://api.example.test";
</script>
<script type="module" src="./app.js"></script>
```

The UI intentionally shows:

Identity → Claim → Verification Event → Evidence → Procedure

It does not calculate a trust score, reputation score or universal truth score.

## Local preview

Serve the web directory through any static HTTP server:

```bash
python -m http.server 4173 --directory web
```

Then open:

`http://127.0.0.1:4173/?id=NTH-000001`

For same-origin API deployment, proxy /v1/ from the static host to the
NOTHING API service.

## Production boundary

Use a CDN/static host with TLS and cache-control for the web assets. Keep the
API origin separate from static content where practical. Public verification
data may be cached by the deployment according to the API freshness contract.
