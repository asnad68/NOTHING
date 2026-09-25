(() => {
  "use strict";

  const DEMO_DATA_URL = "./data/demo-bundle.json";
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
    const response = await fetch(\`\${API_BASE}\${path}\`, {
      headers: { Accept: "application/json" },
      cache: "no-store"
    });
    if (!response.ok) {
      let detail = \`HTTP \${response.status}\`;
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
      fetchJson(\`/v1/identity/\${encodeURIComponent(nothingId)}\`),
      fetchJson(\`/v1/identity/\${encodeURIComponent(nothingId)}/verification-events\`)
    ]);

    const events = eventsPayload.data.events || [];
    const evidenceIds = [
      ...new Set(events.flatMap((event) => event.evidence || []))
    ];
    const procedureKeys = [
      ...new Set(
        events
          .filter((event) => event.procedure?.id && event.procedure?.version)
          .map((event) => \`\${event.procedure.id}@@\${event.procedure.version}\`)
      )
    ];

    const [evidencePayloads, procedurePayloads] = await Promise.all([
      Promise.all(
        evidenceIds.map((id) =>
          fetchJson(\`/v1/evidence/\${encodeURIComponent(id)}\`).catch(() => null)
        )
      ),
      Promise.all(
        procedureKeys.map((key) => {
          const [procedureId, version] = key.split("@@");
          return fetchJson(
            \`/v1/procedures/\${encodeURIComponent(procedureId)}/\${encodeURIComponent(version)}\`
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
    return normalizeDemo(await response.json(), nothingId);
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
        \`\${procedure.id}@@\${procedure.version}\`,
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

        return \`
          <article class="claim-card">
            <div class="claim-top">
              <span class="claim-id">\${escapeHtml(
                claim.claim_id || claim.id
              )}</span>
              <span class="status \${statusClass(
                claim.status || claim.recorded_status
              )}">\${escapeHtml(
                claim.status || claim.recorded_status
              )}</span>
            </div>
            <p class="claim-statement">\${escapeHtml(
              claim.statement
            )}</p>
            <div class="meta-line">
              <span>Current event: \${escapeHtml(
                eventStatus || "NONE"
              )}</span>
              <span>Evidence: \${evidenceIds.length}</span>
            </div>
          </article>\`;
      })
      .join("");

    const sortedEvents = [...events].sort(
      (a, b) =>
        new Date(a.occurred_at) - new Date(b.occurred_at)
    );

    $("#timeline").innerHTML = sortedEvents
      .map((event) => {
        const procedure = procedures.get(
          \`\${event.procedure?.id}@@\${event.procedure?.version}\`
        );
        const evidenceNames = (event.evidence || []).map((id) => {
          const item = evidenceById.get(id);
          return item ? \`\${id} — \${item.type}\` : id;
        });

        return \`
          <article class="event">
            <div class="event-dot" aria-hidden="true"></div>
            <div class="event-body">
              <div class="claim-id">
                \${escapeHtml(event.id || event.event_id)} ·
                \${escapeHtml(formatDate(event.occurred_at))}
              </div>
              <h3>
                \${escapeHtml(event.result?.status || "UNKNOWN")} —
                \${escapeHtml(event.result?.scope || "No declared scope")}
              </h3>
              <p>\${escapeHtml(
                event.result?.reason || "No additional reason recorded."
              )}</p>
              <div class="event-meta">
                <span>Claim: \${escapeHtml(event.claim_id)}</span>
                <span>Procedure: \${escapeHtml(
                  \`\${event.procedure?.id || "unknown"}@\${event.procedure?.version || "?"}\`
                )}</span>
                <span>Verifier: \${escapeHtml(
                  event.verifier?.type || "not specified"
                )}</span>
                <span>Evidence: \${escapeHtml(
                  evidenceNames.join("; ") || "none"
                )}</span>
                \${event.supersedes
                  ? \`<span>Supersedes: \${escapeHtml(
                      event.supersedes
                    )}</span>\`
                  : ""}
                \${procedure
                  ? \`<span>Procedure status: \${escapeHtml(
                      procedure.status
                    )}</span>\`
                  : ""}
              </div>
            </div>
          </article>\`;
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
      \`?id=\${encodeURIComponent(id)}\`
    );
    load();
  });

  if (document.body.dataset.page === "verify") {
    load();
  }
})();