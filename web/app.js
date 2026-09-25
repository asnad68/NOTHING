const API_BASE = (window.NOTHING_API_BASE || "/").replace(/\/$/, "");

const $ = (selector) => document.querySelector(selector);
const form = $("#lookup-form");
const input = $("#nothing-id");
const errorBox = $("#lookup-error");
const statusTitle = $("#status-title");
const statusDetail = $("#status-detail");
const record = $("#record");
const recordId = $("#record-id");
const recordSubject = $("#record-subject");
const recordRevocation = $("#record-revocation");
const claims = $("#claims");
const verification = $("#verification");
const evidence = $("#evidence");
const timeline = $("#timeline");
const meta = $("#meta");

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function text(parent, value, className = "") {
  const node = document.createElement("div");
  node.textContent = value == null ? "" : String(value);
  if (className) node.className = className;
  parent.appendChild(node);
  return node;
}

function setStatus(title, detail) {
  statusTitle.textContent = title;
  statusDetail.textContent = detail;
}

function showError(message) {
  errorBox.hidden = false;
  errorBox.textContent = message;
}

function hideError() {
  errorBox.hidden = true;
  errorBox.textContent = "";
}

function renderVerification(current) {
  clear(verification);
  if (!current) {
    text(verification, "No current verification event is recorded.", "item-muted");
    return;
  }
  const item = document.createElement("div");
  item.className = "item";
  text(item, current.status, "item-title");
  text(item, current.scope, "item-muted");
  text(item, `Event: ${current.event_id}`, "item-muted");
  text(item, `Occurred: ${current.occurred_at}`, "item-muted");
  text(item, `Procedure: ${current.procedure.id} v${current.procedure.version}`, "item-muted");
  const chipRow = document.createElement("div");
  chipRow.className = "chip-row";
  for (const id of current.evidence_ids || []) text(chipRow, id, "chip");
  item.appendChild(chipRow);
  verification.appendChild(item);
}

function renderClaims(data) {
  clear(claims);
  if (!data.length) {
    text(claims, "No claims are recorded.", "item-muted");
    return;
  }
  for (const claim of data) {
    const item = document.createElement("div");
    item.className = "item";
    text(item, claim.statement, "item-title");
    text(item, `Recorded status: ${claim.recorded_status}`, "item-muted");
    if (claim.current_verification) {
      text(item, `Current result: ${claim.current_verification.status}`, "item-muted");
    }
    claims.appendChild(item);
  }
}

function renderEvidence(data) {
  clear(evidence);
  let rendered = false;
  for (const claim of data.claims || []) {
    const current = claim.current_verification;
    if (!current) continue;
    rendered = true;
    const item = document.createElement("div");
    item.className = "item";
    text(item, `Claim ${claim.id}`, "item-title");
    text(item, `Procedure: ${current.procedure.id} v${current.procedure.version}`, "item-muted");
    text(item, "Evidence references", "item-muted");
    const chipRow = document.createElement("div");
    chipRow.className = "chip-row";
    for (const id of current.evidence_ids || []) text(chipRow, id, "chip");
    item.appendChild(chipRow);
    evidence.appendChild(item);
  }
  if (!rendered) {
    text(evidence, "No verification evidence references are recorded.", "item-muted");
  }
}

function renderTimeline(events) {
  clear(timeline);
  if (!events.length) {
    text(timeline, "No verification events are recorded.", "item-muted");
    return;
  }
  const ordered = [...events].sort(
    (a, b) => String(b.occurred_at).localeCompare(String(a.occurred_at))
  );
  for (const event of ordered) {
    const item = document.createElement("div");
    item.className = "item";
    text(item, event.result?.status || "UNKNOWN", "item-title");
    text(item, `${event.occurred_at} · ${event.id}`, "item-muted");
    text(item, `Claim: ${event.claim_id}`, "item-muted");
    text(
      item,
      `Procedure: ${event.procedure?.id || "unknown"} v${event.procedure?.version || "?"}`,
      "item-muted"
    );
    if (event.supersedes) {
      text(item, `Supersedes: ${event.supersedes}`, "item-muted");
    }
    const chipRow = document.createElement("div");
    chipRow.className = "chip-row";
    for (const id of event.evidence || []) text(chipRow, id, "chip");
    item.appendChild(chipRow);
    timeline.appendChild(item);
  }
}

function renderRecord(payload) {
  const data = payload.data;
  record.hidden = false;
  recordId.textContent = data.id;
  recordSubject.textContent = `${data.subject?.name || "Unnamed subject"} · ${data.subject?.type || "unknown type"}`;
  recordRevocation.textContent = data.revocation?.status || "UNKNOWN";
  renderClaims(data.claims || []);
  renderVerification(
    (data.claims || []).find((claim) => claim.current_verification)?.current_verification || null
  );
  renderEvidence(data);
  meta.textContent = `API v${payload.meta?.api_version || "?"} · Protocol ${payload.meta?.protocol_version || "?"}`;
}

async function fetchJson(path) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json" },
    cache: "no-store"
  });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      message = body.detail || message;
    } catch {}
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

async function fetchIdentity(id) {
  return fetchJson(`/v1/identity/${encodeURIComponent(id)}`);
}

async function fetchVerificationEvents(id) {
  return fetchJson(
    `/v1/identity/${encodeURIComponent(id)}/verification-events`
  );
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  hideError();
  record.hidden = true;

  const id = input.value.trim().toUpperCase();
  if (!/^NTH-[0-9]{6}$/.test(id)) {
    showError("Enter a valid NOTHING identifier such as NTH-000001.");
    return;
  }

  setStatus("Resolving", `Looking up ${id}…`);

  try {
    const [payload, eventPayload] = await Promise.all([
      fetchIdentity(id),
      fetchVerificationEvents(id),
    ]);
    renderRecord(payload);
    renderTimeline(eventPayload.data.events || []);
    setStatus(
      "Resolved",
      "The record below reflects the API's current resolved graph and event history."
    );
  } catch (error) {
    const detail = error.status === 404
      ? "No public identity record was found for this identifier."
      : `The verifier could not load the record: ${error.message}`;
    setStatus("Lookup failed", detail);
    showError(detail);
  }
});

const params = new URLSearchParams(window.location.search);
const initialId = params.get("id");
if (initialId) {
  input.value = initialId.toUpperCase();
  form.requestSubmit();
}
