(() => {
  "use strict";

  const DATA_URL = "./data/demo-bundle.json";
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
      claimEvents.filter((event) => event.supersedes).map((event) => event.supersedes)
    );

    return claimEvents.find((event) => !superseded.has(event.event_id)) || null;
  }

  function showState({ loading, result, notFound }) {
    $("#loading").hidden = !loading;
    $("#result").hidden = !result;
    $("#not-found").hidden = !notFound;
  }

  function render(bundle, nothingId) {
    const identity = bundle.identities.find((item) => item.nothing_id === nothingId);

    if (!identity) {
      showState({ loading: false, result: false, notFound: true });
      return;
    }

    const events = bundle.verification_events.filter((event) => event.subject === identity.nothing_id);
    const evidenceById = new Map(bundle.evidence.map((item) => [item.evidence_id, item]));
    const procedures = new Map(
      bundle.procedure_registry.procedures.map((procedure) => [
        procedure.id + "@" + procedure.version,
        procedure
      ])
    );

    $("#identity-id").textContent = identity.nothing_id;
    $("#identity-name").textContent = identity.subject.name;
    $("#identity-type").textContent = identity.subject.type.replaceAll("_", " ");

    const claimStatuses = identity.claims.map((claim) => {
      const event = currentEvent(events, claim.claim_id);
      return event?.result?.status || claim.status;
    });

    const currentRecordState = identity.revocation?.status === "REVOKED"
      ? "REVOKED"
      : claimStatuses.some((status) => status === "VERIFIED" || status === "SOURCE-VERIFIED")
        ? "CLAIMS PRESENT"
        : "CLAIMS RECORDED";

    const stateEl = $("#record-state");
    stateEl.textContent = currentRecordState;
    stateEl.className = "status " + (
      currentRecordState === "REVOKED"
        ? "status-bad"
        : currentRecordState === "CLAIMS PRESENT"
          ? "status-ok"
          : "status-neutral"
    );

    const warning = $("#consistency-warning");
    const mismatches = identity.claims.filter((claim) => {
      const event = currentEvent(events, claim.claim_id);
      return event && event.result.status !== claim.status;
    });

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

    $("#claims").innerHTML = identity.claims.map((claim) => {
      const event = currentEvent(events, claim.claim_id);
      const eventStatus = event?.result?.status;
      const evidenceIds = event?.evidence || [];
      return `
        <article class="claim-card">
          <div class="claim-top">
            <span class="claim-id">${escapeHtml(claim.claim_id)}</span>
            <span class="status ${statusClass(claim.status)}">${escapeHtml(claim.status)}</span>
          </div>
          <p class="claim-statement">${escapeHtml(claim.statement)}</p>
          <div class="meta-line">
            <span>Current event: ${escapeHtml(eventStatus || "NONE")}</span>
            <span>Evidence: ${evidenceIds.length}</span>
          </div>
        </article>`;
    }).join("");

    const sortedEvents = [...events].sort(
      (a, b) => new Date(a.occurred_at) - new Date(b.occurred_at)
    );

    $("#timeline").innerHTML = sortedEvents.map((event) => {
      const procedure = procedures.get(event.procedure.id + "@" + event.procedure.version);
      const evidenceNames = (event.evidence || []).map((id) => {
        const item = evidenceById.get(id);
        return item ? `${id} — ${item.type}` : `${id} — unresolved`;
      });

      return `
        <article class="event">
          <div class="event-dot" aria-hidden="true"></div>
          <div class="event-body">
            <div class="claim-id">${escapeHtml(event.event_id)} · ${escapeHtml(formatDate(event.occurred_at))}</div>
            <h3>${escapeHtml(event.result.status)} — ${escapeHtml(event.result.scope)}</h3>
            <p>${escapeHtml(event.result.reason || "No additional reason recorded.")}</p>
            <div class="event-meta">
              <span>Claim: ${escapeHtml(event.claim_id)}</span>
              <span>Procedure: ${escapeHtml(event.procedure.id + "@" + event.procedure.version)}</span>
              <span>Verifier: ${escapeHtml(event.verifier?.type || "not specified")}</span>
              <span>Evidence: ${escapeHtml(evidenceNames.join("; ") || "none")}</span>
              ${event.supersedes ? `<span>Supersedes: ${escapeHtml(event.supersedes)}</span>` : ""}
              ${procedure ? `<span>Procedure status: ${escapeHtml(procedure.status)}</span>` : ""}
            </div>
          </div>
        </article>`;
    }).join("");

    $("#raw-json").textContent = JSON.stringify(bundle, null, 2);
    showState({ loading: false, result: true, notFound: false });
  }

  async function load() {
    showState({ loading: true, result: false, notFound: false });
    try {
      const response = await fetch(DATA_URL, { cache: "no-store" });
      if (!response.ok) throw new Error("Unable to load demo dataset.");
      const bundle = await response.json();
      const requested = new URLSearchParams(window.location.search).get("id") || "NTH-000001";
      const nothingId = requested.trim().toUpperCase();
      $("#search-id").value = nothingId;
      if (!ID_PATTERN.test(nothingId)) {
        showState({ loading: false, result: false, notFound: true });
        return;
      }
      render(bundle, nothingId);
    } catch (error) {
      $("#not-found").hidden = false;
      $("#not-found").innerHTML =
        "<strong>Unable to load the prototype dataset.</strong><p>" +
        escapeHtml(error.message) + "</p>";
      showState({ loading: false, result: false, notFound: true });
    }
  }

  $("#search-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const id = $("#search-id").value.trim().toUpperCase();
    window.history.replaceState({}, "", `?id=${encodeURIComponent(id)}`);
    load();
  });

  if (document.body.dataset.page === "verify") load();
})();
