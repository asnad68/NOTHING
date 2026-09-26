(() => {
  "use strict";

  const DEMO_DATA_URL = "./data/demo-bundle.json";
  const DEMO_PROOF_URL = "./data/demo-proof.json";
  const DEMO_ISSUER_REGISTRY_URL = "./.well-known/nothing-keys.json";
  const API_BASE = String(window.NOTHING_API_BASE || "").replace(/\/$/, "");
  const ID_PATTERN = /^NTH-[0-9]{6}$/;

  const $ = (selector) => document.querySelector(selector);

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat("en", {
      dateStyle: "medium",
      timeStyle: "short"
    }).format(date);
  }

  function statusClass(status) {
    if (status === "VERIFIED" || status === "SOURCE-VERIFIED") return "status-ok";
    if (status === "SELF-CLAIMED" || status === "INCONCLUSIVE") return "status-warn";
    if (status === "REVOKED" || status === "NOT-VERIFIED") return "status-bad";
    return "status-neutral";
  }

  function currentEvent(events, claimId) {
    const claimEvents = events
      .filter((event) => event.claim_id === claimId)
      .sort((a, b) => new Date(b.occurred_at) - new Date(a.occurred_at));

    const superseded = new Set(
      claimEvents
        .filter((event) => event.supersedes)
        .map((event) => event.supersedes)
    );

    return claimEvents.find((event) => !superseded.has(event.id || event.event_id)) || null;
  }

  function normalizeDemo(bundle, nothingId) {
    const identity = bundle.identities.find(
      (item) => item.nothing_id === nothingId
    );
    if (!identity) return null;

    const events = bundle.verification_events.filter(
      (event) => event.subject === identity.nothing_id
    );
    const evidence = bundle.evidence.filter((item) =>
      events.some((event) => (event.evidence || []).includes(item.evidence_id))
    );

    return {
      mode: "demo",
      nothingId,
      identity,
      events,
      evidence,
      procedures: bundle.procedure_registry?.procedures || [],
      metadata: {
        api_version: "demo",
        protocol_version: bundle.bundle_version || "0.1"
      }
    };
  }

  async function fetchJson(path) {
    const response = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: "application/json" },
      cache: "no-store"
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        detail = body.detail || detail;
      } catch {}
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  async function loadLive(nothingId) {
    const [identityPayload, eventsPayload] = await Promise.all([
      fetchJson(`/v1/identity/${encodeURIComponent(nothingId)}`),
      fetchJson(`/v1/identity/${encodeURIComponent(nothingId)}/verification-events`)
    ]);

    const events = eventsPayload.data.events || [];
    const evidenceIds = [
      ...new Set(events.flatMap((event) => event.evidence || []))
    ];
    const procedureKeys = [
      ...new Set(
        events
          .filter((event) => event.procedure?.id && event.procedure?.version)
          .map((event) => `${event.procedure.id}@@${event.procedure.version}`)
      )
    ];

    const [evidencePayloads, procedurePayloads] = await Promise.all([
      Promise.all(
        evidenceIds.map((id) =>
          fetchJson(`/v1/evidence/${encodeURIComponent(id)}`).catch(() => null)
        )
      ),
      Promise.all(
        procedureKeys.map((key) => {
          const [procedureId, version] = key.split("@@");
          return fetchJson(
            `/v1/procedures/${encodeURIComponent(procedureId)}/${encodeURIComponent(version)}`
          ).catch(() => null);
        })
      )
    ]);

    return {
      mode: "live",
      nothingId,
      identity: identityPayload.data,
      events,
      evidence: evidencePayloads
        .map((payload) => payload?.data)
        .filter(Boolean),
      procedures: procedurePayloads
        .map((payload) => payload?.data)
        .filter(Boolean),
      metadata: identityPayload.meta || {}
    };
  }

  async function loadDemo(nothingId) {
    const response = await fetch(DEMO_DATA_URL, { cache: "no-store" });
    if (!response.ok) throw new Error("Unable to load demo dataset.");
    const bundle = await response.json();

    const [proofResponse, registryResponse] = await Promise.all([
      fetch(DEMO_PROOF_URL, { cache: "no-store" }),
      fetch(DEMO_ISSUER_REGISTRY_URL, { cache: "no-store" })
    ]);

    if (!proofResponse.ok) throw new Error("Unable to load demo proof.");
    if (!registryResponse.ok) throw new Error("Unable to load demo issuer registry.");

    const record = normalizeDemo(bundle, nothingId);
    if (!record) return null;

    record.proof = await proofResponse.json();
    record.issuerRegistry = await registryResponse.json();
    return record;
  }

  function base64UrlToBytes(value) {
    const normalized = String(value).replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized + "=".repeat((4 - normalized.length % 4) % 4);
    const binary = window.atob(padded);
    return Uint8Array.from(binary, (char) => char.charCodeAt(0));
  }

  function canonicalize(value) {
    if (value === null) return "null";
    if (typeof value === "boolean") return value ? "true" : "false";
    if (typeof value === "number") {
      if (!Number.isFinite(value) || !Number.isInteger(value)) {
        throw new Error("This proof profile rejects non-integer numbers.");
      }
      if (Math.abs(value) > Number.MAX_SAFE_INTEGER) {
        throw new Error("This proof profile rejects unsafe integers.");
      }
      return String(value);
    }
    if (typeof value === "string") return JSON.stringify(value);
    if (Array.isArray(value)) return "[" + value.map(canonicalize).join(",") + "]";
    if (typeof value === "object") {
      const keys = Object.keys(value).sort((a, b) => {
        const aa = Array.from(a).map((ch) => ch.charCodeAt(0));
        const bb = Array.from(b).map((ch) => ch.charCodeAt(0));
        const len = Math.min(aa.length, bb.length);
        for (let i = 0; i < len; i += 1) {
          if (aa[i] !== bb[i]) return aa[i] - bb[i];
        }
        return aa.length - bb.length;
      });
      return "{" + keys.map((key) => JSON.stringify(key) + ":" + canonicalize(value[key])).join(",") + "}";
    }
    throw new Error("Unsupported value in proof canonicalization.");
  }

  async function sha256Hex(value) {
    const bytes = new TextEncoder().encode(canonicalize(value));
    const digest = await window.crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
  }

  async function verifyDemoProof(record) {
    const proof = record.proof;
    const issuerRegistry = record.issuerRegistry;

    if (!proof || !issuerRegistry) {
      return { state: "UNAVAILABLE", reason: "No proof material is published for this record." };
    }
    if (proof.resource_id !== record.identity.nothing_id || proof.resource_type !== "identity") {
      return { state: "INVALID", reason: "Proof resource does not match the displayed identity." };
    }

    const calculatedHash = await sha256Hex(record.identity);
    if (calculatedHash !== proof.resource_hash) {
      return {
        state: "INVALID",
        reason: "The resource hash does not match the published identity record.",
        calculatedHash
      };
    }

    const issuerId = proof.issuer && proof.issuer.issuer_id;
    const keyId = proof.issuer && proof.issuer.key_id;
    const issuer = (issuerRegistry.issuers || []).find((item) => item.issuer_id === issuerId);
    const key = issuer && (issuer.keys || []).find((item) => item.key_id === keyId);

    if (!issuer || !key) {
      return { state: "INVALID", reason: "The proof issuer/key is not present in the published registry." };
    }
    if (!["ACTIVE", "DEMO"].includes(issuer.status) || key.status !== "ACTIVE") {
      return { state: "INVALID", reason: "The proof issuer or key is not active." };
    }

    const signingDocument = {
      envelope_id: proof.envelope_id,
      version: proof.version,
      resource_type: proof.resource_type,
      resource_id: proof.resource_id,
      resource_hash: proof.resource_hash,
      issuer: proof.issuer,
      proof: {
        type: proof.proof.type,
        created: proof.proof.created,
        proof_purpose: proof.proof.proof_purpose
      }
    };

    if (!window.crypto || !window.crypto.subtle) {
      return {
        state: "UNAVAILABLE",
        reason: "Web Crypto is unavailable in this browser.",
        calculatedHash,
        issuerId,
        keyId
      };
    }

    try {
      const publicKey = await window.crypto.subtle.importKey(
        "raw",
        base64UrlToBytes(key.public_key),
        { name: "Ed25519" },
        false,
        ["verify"]
      );
      const validSignature = await window.crypto.subtle.verify(
        { name: "Ed25519" },
        publicKey,
        base64UrlToBytes(proof.proof.signature),
        new TextEncoder().encode(canonicalize(signingDocument))
      );

      return {
        state: validSignature ? "VALID" : "INVALID",
        reason: validSignature
          ? "SHA-256 resource binding and Ed25519 signature verified in this browser."
          : "Ed25519 signature verification failed.",
        calculatedHash,
        issuerId,
        keyId
      };
    } catch (error) {
      return {
        state: "UNAVAILABLE",
        reason: "This browser could not execute Ed25519 verification. The signed envelope remains available for independent verification.",
        calculatedHash,
        issuerId,
        keyId
      };
    }
  }

  function renderProof(result) {
    const state = result.state || "UNAVAILABLE";
    const status = $("#proof-status");
    status.textContent = state;
    status.className =
      "status " +
      (state === "VALID"
        ? "status-ok"
        : state === "INVALID"
          ? "status-bad"
          : "status-neutral");

    const details = $("#proof-details");
    const rows = [
      ["Resource hash", result.calculatedHash || "—"],
      ["Issuer", result.issuerId || "—"],
      ["Key", result.keyId || "—"],
      ["Result", result.reason || "—"]
    ];
    details.innerHTML = rows
      .map(
        ([label, value]) =>
          '<div class="proof-row"><span>' +
          escapeHtml(label) +
          "</span><code>" +
          escapeHtml(value) +
          "</code></div>"
      )
      .join("");
  }

  function showState({ loading, result, notFound }) {
    $("#loading").hidden = !loading;
    $("#result").hidden = !result;
    $("#not-found").hidden = !notFound;
  }

  function render(record) {
    const identity = record.identity;
    const events = record.events;
    const evidenceById = new Map(
      record.evidence.map((item) => [item.evidence_id || item.id, item])
    );
    const procedures = new Map(
      record.procedures.map((procedure) => [
        `${procedure.id}@@${procedure.version}`,
        procedure
      ])
    );

    $("#identity-id").textContent = identity.id || identity.nothing_id;
    $("#identity-name").textContent =
      identity.subject?.name || "Unnamed subject";
    $("#identity-type").textContent =
      (identity.subject?.type || "unknown type").replaceAll("_", " ");

    const claims = identity.claims || [];
    const claimStatuses = claims.map((claim) => {
      const current = claim.current_verification;
      return current?.status || claim.status;
    });

    const currentRecordState =
      identity.revocation?.status === "REVOKED"
        ? "REVOKED"
        : claimStatuses.some(
            (status) => status === "VERIFIED" || status === "SOURCE-VERIFIED"
          )
          ? "CLAIMS PRESENT"
          : "CLAIMS RECORDED";

    const stateEl = $("#record-state");
    stateEl.textContent = currentRecordState;
    stateEl.className =
      "status " +
      (currentRecordState === "REVOKED"
        ? "status-bad"
        : currentRecordState === "CLAIMS PRESENT"
          ? "status-ok"
          : "status-neutral");

    const badge = $("#data-mode");
    badge.textContent = record.mode === "live" ? "LIVE API" : "DEMO DATA";

    const mismatches = record.mode === "demo"
      ? claims.filter((claim) => {
          const event = currentEvent(events, claim.claim_id);
          return event && event.result.status !== claim.status;
        })
      : [];

    const warning = $("#consistency-warning");
    if (mismatches.length) {
      warning.hidden = false;
      warning.innerHTML =
        "<strong>Status synchronization note.</strong>" +
        "<p>The demo identity record and its current verification event disagree for " +
        escapeHtml(mismatches.map((claim) => claim.claim_id).join(", ")) +
        ". NOTHING exposes the mismatch instead of silently rewriting the identity record.</p>";
    } else {
      warning.hidden = true;
      warning.textContent = "";
    }

    $("#claims").innerHTML = claims
      .map((claim) => {
        const event = claim.current_verification
          ? {
              result: { status: claim.current_verification.status },
              evidence: claim.current_verification.evidence_ids || [],
              occurred_at: claim.current_verification.occurred_at,
              event_id: claim.current_verification.event_id
            }
          : currentEvent(events, claim.claim_id);

        const eventStatus = event?.result?.status;
        const evidenceIds = event?.evidence || event?.evidence_ids || [];

        return `
          <article class="claim-card">
            <div class="claim-top">
              <span class="claim-id">${escapeHtml(
                claim.claim_id || claim.id
              )}</span>
              <span class="status ${statusClass(
                claim.status || claim.recorded_status
              )}">${escapeHtml(
                claim.status || claim.recorded_status
              )}</span>
            </div>
            <p class="claim-statement">${escapeHtml(
              claim.statement
            )}</p>
            <div class="meta-line">
              <span>Current event: ${escapeHtml(
                eventStatus || "NONE"
              )}</span>
              <span>Evidence: ${evidenceIds.length}</span>
            </div>
          </article>`;
      })
      .join("");

    const sortedEvents = [...events].sort(
      (a, b) =>
        new Date(a.occurred_at) - new Date(b.occurred_at)
    );

    $("#timeline").innerHTML = sortedEvents
      .map((event) => {
        const procedure = procedures.get(
          `${event.procedure?.id}@@${event.procedure?.version}`
        );
        const evidenceNames = (event.evidence || []).map((id) => {
          const item = evidenceById.get(id);
          return item ? `${id} — ${item.type}` : id;
        });

        return `
          <article class="event">
            <div class="event-dot" aria-hidden="true"></div>
            <div class="event-body">
              <div class="claim-id">
                ${escapeHtml(event.id || event.event_id)} ·
                ${escapeHtml(formatDate(event.occurred_at))}
              </div>
              <h3>
                ${escapeHtml(event.result?.status || "UNKNOWN")} —
                ${escapeHtml(event.result?.scope || "No declared scope")}
              </h3>
              <p>${escapeHtml(
                event.result?.reason || "No additional reason recorded."
              )}</p>
              <div class="event-meta">
                <span>Claim: ${escapeHtml(event.claim_id)}</span>
                <span>Procedure: ${escapeHtml(
                  `${event.procedure?.id || "unknown"}@${event.procedure?.version || "?"}`
                )}</span>
                <span>Verifier: ${escapeHtml(
                  event.verifier?.type || "not specified"
                )}</span>
                <span>Evidence: ${escapeHtml(
                  evidenceNames.join("; ") || "none"
                )}</span>
                ${event.supersedes
                  ? `<span>Supersedes: ${escapeHtml(
                      event.supersedes
                    )}</span>`
                  : ""}
                ${procedure
                  ? `<span>Procedure status: ${escapeHtml(
                      procedure.status
                    )}</span>`
                  : ""}
              </div>
            </div>
          </article>`;
      })
      .join("");

    $("#raw-json").textContent = JSON.stringify(
      {
        identity,
        verification_events: events,
        evidence: record.evidence,
        procedures: record.procedures
      },
      null,
      2
    );

    showState({ loading: false, result: true, notFound: false });
  }

  async function load() {
    const requested =
      new URLSearchParams(window.location.search).get("id") ||
      "NTH-000001";
    const nothingId = requested.trim().toUpperCase();

    $("#search-id").value = nothingId;

    if (!ID_PATTERN.test(nothingId)) {
      showState({ loading: false, result: false, notFound: true });
      return;
    }

    showState({ loading: true, result: false, notFound: false });

    try {
      if (API_BASE) {
        try {
          render(await loadLive(nothingId));
          return;
        } catch (liveError) {
          if (liveError.status && liveError.status !== 404) {
            throw liveError;
          }
        }
      }

      const demo = await loadDemo(nothingId);
      if (!demo) {
        showState({ loading: false, result: false, notFound: true });
        return;
      }
      render(demo);
    } catch (error) {
      $("#not-found").hidden = false;
      $("#not-found").innerHTML =
        "<strong>Unable to load the verification record.</strong><p>" +
        escapeHtml(error.message) +
        "</p>";
      showState({ loading: false, result: false, notFound: true });
    }
  }

  $("#search-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const id = $("#search-id").value.trim().toUpperCase();
    window.history.replaceState(
      {},
      "",
      `?id=${encodeURIComponent(id)}`
    );
    load();
  });

  if (document.body.dataset.page === "verify") {
    load();
  }
})();