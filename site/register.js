(() => {
  "use strict";
  const API_BASE = String(window.NOTHING_ENROLLMENT_API_BASE || window.NOTHING_API_BASE || "").replace(/\/$/, "");
  const WALLET_ENABLED = window.NOTHING_WALLET_ENABLED === true;
  const IDENTITY_CONTROL_ENABLED = window.NOTHING_IDENTITY_CONTROL_ENABLED === true;
  const WALLET_AUTH_ENABLED = window.NOTHING_WALLET_AUTH_ENABLED === true;
  const GOOGLE_CLIENT_ID = String(window.NOTHING_GOOGLE_CLIENT_ID || "").trim();
  const $ = (id) => document.getElementById(id);
  const providers = new Map();
  let selectedProvider = null;
  let walletAddress = null;
  let invoice = null;
  let domainChallenge = null;
  let officialAuthorizationChallengeId = null;
  let googleInitialized = false;

  const htmlEscape = (v) => String(v ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#039;");
  const xmlEscape = (v) => htmlEscape(v);
  const lines = (v) => String(v || "").split(/[\n,]+/).map(s => s.trim()).filter(Boolean);

  function decodeDraft() {
    const raw = new URLSearchParams(location.hash.slice(1)).get("draft");
    if (!raw) return null;
    try {
      const bytes = Uint8Array.from(atob(raw.replaceAll("-","+").replaceAll("_","/")+"=".repeat((4-raw.length%4)%4)), c => c.charCodeAt(0));
      const draft = JSON.parse(new TextDecoder().decode(bytes));
      history.replaceState(null, "", location.pathname + location.search);
      return draft;
    } catch { return null; }
  }

  function isOfficialMode() {
    return $("registrationMode")?.value === "official";
  }

  function requestedDomain() {
    const value = $("organizationDomain").value.trim().toLowerCase().replace(/\.$/, "");
    if (!value || !value.includes(".") || /\s/.test(value)) {
      throw new Error("Enter the organization domain, for example example.com.");
    }
    return value;
  }

  function officialStatus(message, ok = false) {
    const el = $("official-status");
    el.textContent = message;
    el.className = "status " + (ok ? "status-ok" : "status-neutral");
  }

  async function getDomainChallenge() {
    if (!IDENTITY_CONTROL_ENABLED) throw new Error("Official organization registration is disabled on this deployment.");
    const domain = requestedDomain();
    const response = await apiJson("/v1/organization/domain-challenge?domain=" + encodeURIComponent(domain));
    domainChallenge = response.data || response;
    $("domain-record-name").textContent = domainChallenge.record_name;
    $("domain-record-value").textContent = domainChallenge.record_value;
    $("domain-challenge").hidden = false;
    $("verify-domain").disabled = false;
    $("domain-state").textContent = "Add the TXT record exactly as shown, then verify domain control.";
    officialAuthorizationChallengeId = null;
    officialStatus("Domain challenge issued. Organization control is not yet verified.");
  }

  async function verifyDomain() {
    if (!domainChallenge?.challenge_digest) throw new Error("Get a DNS challenge first.");
    const domain = requestedDomain();
    const response = await apiJson("/v1/organization/domain-verify", {
      method: "POST",
      body: JSON.stringify({
        domain,
        challenge: domainChallenge.challenge
      })
    });
    const data = response.data || response;
    if (!data.domain_controlled) {
      $("domain-state").textContent = "DNS verification did not find the required TXT record yet.";
      officialStatus("Domain not independently verified.");
      return false;
    }
    $("domain-state").textContent = "Domain control verified for " + data.domain + ".";
    officialStatus("Domain control verified. Complete Google or wallet authorization.", true);
    return true;
  }

  async function loadGoogleSignIn() {
    if (googleInitialized || !IDENTITY_CONTROL_ENABLED || !GOOGLE_CLIENT_ID) {
      if (IDENTITY_CONTROL_ENABLED && !GOOGLE_CLIENT_ID) {
        $("google-state").textContent = "Google sign-in is not configured on this deployment.";
      }
      return;
    }
    await new Promise((resolve, reject) => {
      const existing = document.querySelector('script[data-nothing-google="1"]');
      if (existing) {
        existing.addEventListener("load", resolve, {once:true});
        existing.addEventListener("error", reject, {once:true});
        return;
      }
      const script = document.createElement("script");
      script.src = "https://accounts.google.com/gsi/client";
      script.async = true;
      script.defer = true;
      script.dataset.nothingGoogle = "1";
      script.onload = resolve;
      script.onerror = () => reject(new Error("Google sign-in library could not be loaded."));
      document.head.appendChild(script);
    });
    if (!window.google?.accounts?.id) throw new Error("Google Identity Services is unavailable.");
    window.google.accounts.id.initialize({
      client_id: GOOGLE_CLIENT_ID,
      callback: async (response) => {
        try {
          await authorizeWithGoogle(response.credential);
        } catch (e) {
          $("google-state").textContent = e.message;
          officialStatus("Google authorization failed.");
        }
      },
      auto_select: false,
      cancel_on_tap_outside: true
    });
    $("google-signin-button").replaceChildren();
    window.google.accounts.id.renderButton($("google-signin-button"), {
      type: "standard",
      theme: "outline",
      size: "large",
      text: "signin_with",
      shape: "rectangular",
      width: 320
    });
    googleInitialized = true;
  }

  async function ensureWalletForAuth() {
    if (!WALLET_AUTH_ENABLED) throw new Error("Wallet sign-in is disabled on this deployment.");
    if (!selectedProvider || !walletAddress) {
      if (!providers.size) throw new Error("No compatible wallet was found.");
      await connect([...providers.keys()][0], true);
    }
    if (!selectedProvider || !walletAddress) throw new Error("Connect a wallet first.");
  }

  async function authorizeWithWallet() {
    if (!WALLET_AUTH_ENABLED) throw new Error("Wallet sign-in is disabled on this deployment.");
    if (!domainChallenge?.challenge) throw new Error("Get the organization DNS challenge first.");
    if (!(await verifyDomain())) throw new Error("Verify organization domain control first.");
    await ensureWalletForAuth();
    const challengeResponse = await apiJson("/v1/auth/wallet/challenge", {
      method: "POST",
      body: JSON.stringify({wallet_address: walletAddress})
    });
    const challenge = challengeResponse.data || challengeResponse;
    const signature = await selectedProvider.request({
      method: "personal_sign",
      params: [challenge.message, walletAddress]
    });
    const response = await apiJson("/v1/organization/wallet-authorize", {
      method: "POST",
      body: JSON.stringify({
        challenge_id: challenge.challenge_id,
        message: challenge.message,
        signature,
        domain_challenge: domainChallenge.challenge,
        requested_domain: requestedDomain(),
        registration: readDraft()
      })
    });
    const data = response.data || response;
    officialAuthorizationChallengeId = data.challenge_id;
    $("wallet-auth-state").textContent = "Wallet authentication accepted; authorization is bound to this registration.";
    officialStatus("Official organization authorization completed with wallet + DNS control.", true);
    if (WALLET_ENABLED && window.liveConfig?.enabled) await requestQuote(await selectedProvider.request({method:"eth_chainId"}));
  }

  async function authorizeWithGoogle(idToken) {
    if (!IDENTITY_CONTROL_ENABLED || !GOOGLE_CLIENT_ID) throw new Error("Google official registration is not configured.");
    if (!domainChallenge?.challenge) throw new Error("Get the organization DNS challenge first.");
    if (!(await verifyDomain())) throw new Error("Verify organization domain control first.");
    $("google-state").textContent = "Verifying Google Workspace identity…";
    const response = await apiJson("/v1/organization/google-authorize", {
      method: "POST",
      body: JSON.stringify({
        id_token: idToken,
        domain_challenge: domainChallenge.challenge,
        requested_domain: requestedDomain(),
        registration: readDraft()
      })
    });
    const data = response.data || response;
    officialAuthorizationChallengeId = data.authorization_challenge_id;
    $("google-state").textContent = "Google Workspace identity accepted; authorization is bound to this registration.";
    officialStatus("Official organization authorization completed with Google Workspace + DNS control.", true);
  }

  async function setRegistrationMode() {
    officialAuthorizationChallengeId = null;
    domainChallenge = null;
    $("official-box").hidden = !isOfficialMode();
    if (!isOfficialMode()) return;
    if (!IDENTITY_CONTROL_ENABLED) {
      officialStatus("Official registration is disabled on this deployment.");
      return;
    }
    const domains = lines($("domains").value);
    if (!$("organizationDomain").value && domains[0]) $("organizationDomain").value = domains[0];
    try { await loadGoogleSignIn(); } catch (e) { $("google-state").textContent = e.message; }
  }

  function readDraft() {
    const name = $("brandName").value.trim();
    if (!name) throw new Error("Business / brand name is required.");
    return {
      name,
      type: $("businessType").value,
      website: $("website").value.trim(),
      domains: lines($("domains").value),
      channels: lines($("channels").value),
      description: $("description").value.trim()
    };
  }

  function fillDraft(d) {
    if (!d) return;
    $("brandName").value = d.name || "";
    $("businessType").value = d.type || "business";
    $("website").value = d.website || "";
    $("domains").value = (d.domains || []).join("\n");
    $("channels").value = (d.channels || []).join("\n");
    $("description").value = d.description || "";
  }

  function walletLabel(info) {
    const text = (info?.name || "") + " " + (info?.rdns || "");
    if (/metamask/i.test(text)) return "MetaMask";
    if (/trust/i.test(text)) return "Trust Wallet";
    return info?.name || "Compatible wallet";
  }

  function renderWallets() {
    const box = $("wallet-list");
    if (!providers.size) {
      box.innerHTML = '<div class="muted">No compatible injected wallet found. Install MetaMask, Trust Wallet or another EIP-1193 wallet.</div>';
      return;
    }
    box.innerHTML = [...providers.entries()].map(([key, item]) => {
      const label = htmlEscape(walletLabel(item.info));
      return '<div class="wallet-option"><div><div class="wallet-name">' + label +
        '</div><div class="wallet-meta">' + htmlEscape(key) +
        '</div></div><button class="secondary connect-wallet" data-key="' + htmlEscape(key) + '">Connect</button></div>';
    }).join("");
    box.querySelectorAll(".connect-wallet").forEach((button) => {
      button.addEventListener("click", async () => {
        try { await connect(button.dataset.key); }
        catch (e) { $("wallet-state").textContent = e.message; }
      });
    });
  }

  async function connect(key, authOnly = false) {
    if (!WALLET_ENABLED && !WALLET_AUTH_ENABLED) throw new Error("Wallet access is disabled on this deployment pending security review.");
    if (!window.isSecureContext && location.hostname !== "localhost") throw new Error("Wallet connection requires a secure HTTPS context.");
    const item = providers.get(key);
    if (!item) return;
    selectedProvider = item.provider;
    const accounts = await selectedProvider.request({method:"eth_requestAccounts"});
    walletAddress = accounts?.[0] || null;
    if (!walletAddress) throw new Error("Wallet returned no account.");
    const chainId = await selectedProvider.request({method:"eth_chainId"});
    $("wallet-state").textContent = walletLabel(item.info) + " · " + walletAddress + " · chain " + chainId;
    if (WALLET_ENABLED && window.liveConfig?.enabled && !authOnly) await requestQuote(chainId);
  }

  async function discoverWallets() {
    window.addEventListener("eip6963:announceProvider", (event) => {
      const detail = event.detail || {};
      if (detail?.info?.rdns && detail?.provider) {
        providers.set(detail.info.rdns, {info: detail.info, provider: detail.provider});
        renderWallets();
      }
    });
    window.dispatchEvent(new Event("eip6963:requestProvider"));
    setTimeout(() => {
      if (!providers.size && window.ethereum) {
        providers.set("legacy-window.ethereum", {
          info: {name:"Injected wallet", rdns:"legacy"},
          provider: window.ethereum
        });
        renderWallets();
      }
    }, 900);
    setTimeout(renderWallets, 1200);
  }

  async function apiJson(path, options = {}) {
    if (!API_BASE) throw new Error("NOTHING enrollment API is not configured on this deployment.");
    const response = await fetch(API_BASE + path, {
      ...options,
      headers: {"Accept":"application/json","Content-Type":"application/json", ...(options.headers || {})}
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body?.error?.detail || body?.detail || ("HTTP " + response.status));
    return body;
  }

  async function loadConfig() {
    if (!API_BASE || !WALLET_ENABLED) {
      $("registration-state").textContent = WALLET_ENABLED ? "PAYMENT GATED" : "WALLET DISABLED";
      $("registration-state").className = "status status-neutral";
      return;
    }
    try {
      const payload = await apiJson("/v1/enrollment/config");
      window.liveConfig = payload.data || payload;
      if (window.liveConfig.enabled) {
        $("registration-state").textContent = "LIVE SERVICE";
        $("registration-state").className = "status status-ok";
        $("payment-box").hidden = false;
        $("payment-copy").textContent = "Connect a wallet to request the current registration invoice.";
      } else {
        $("registration-state").textContent = "PAYMENT GATED";
        $("registration-state").className = "status status-neutral";
      }
    } catch (e) {
      $("registration-state").textContent = "SERVICE UNAVAILABLE";
      $("registration-state").className = "status status-neutral";
      $("payment-status").textContent = e.message;
    }
  }

  async function requestQuote(chainId) {
    const response = await apiJson("/v1/enrollment/quote", {
      method:"POST",
      body:JSON.stringify({
        registration: readDraft(),
        wallet_address: walletAddress,
        chain_id: chainId
      })
    });
    invoice = response.data || response;
    $("payment-box").hidden = false;
    $("payment-copy").textContent =
      invoice.amount_display + " " + invoice.asset + " · " + invoice.network +
      " · recipient " + invoice.recipient;
    $("payment-status").textContent = "Invoice expires at " + invoice.expires_at + ".";
  }

  const utf8Hex = (value) => {
    const bytes = new TextEncoder().encode(String(value));
    return "0x" + [...bytes].map(b => b.toString(16).padStart(2, "0")).join("");
  };

  const atomicHex = (n) => {
    const value = BigInt(String(n));
    if (value <= 0n) throw new Error("Payment amount must be positive.");
    return "0x" + value.toString(16);
  };

  async function pay() {
    if (!WALLET_ENABLED) throw new Error("Wallet payments are disabled on this deployment.");
    if (!selectedProvider || !walletAddress) throw new Error("Connect a wallet first.");
    if (!invoice) throw new Error("Request an invoice first.");
    if (!/^0x[0-9a-fA-F]{40}$/.test(String(invoice.recipient || ""))) throw new Error("The server returned an invalid payment recipient.");
    if (!/^0x[0-9a-fA-F]+$/.test(String(invoice.chain_id || ""))) throw new Error("The server returned an invalid chain identifier.");
    const chainId = await selectedProvider.request({method:"eth_chainId"});
    if (String(chainId).toLowerCase() !== String(invoice.chain_id).toLowerCase()) {
      throw new Error("Switch the wallet to " + invoice.chain_id + " and try again.");
    }
    const txHash = await selectedProvider.request({
      method:"eth_sendTransaction",
      params:[{
        from: walletAddress,
        to: invoice.recipient,
        value: atomicHex(invoice.amount_atomic),
        data: invoice.routing_reference ? utf8Hex("NOTHING|" + invoice.routing_reference) : "0x"
      }]
    });
    $("payment-status").textContent = "Transaction submitted. NOTHING is verifying it on-chain…";
    const response = await apiJson("/v1/enrollment/complete", {
      method:"POST",
      body:JSON.stringify({
        invoice_id: invoice.invoice_id,
        registration: readDraft(),
        wallet_address: walletAddress,
        tx_hash: txHash,
        ...(officialAuthorizationChallengeId ? {authorization_challenge_id: officialAuthorizationChallengeId} : {})
      })
    });
    const result = response.data || response;
    showResult(result.identity, result.verify_url);
    $("payment-status").textContent = "Payment verified and registration recorded.";
  }

  function badgeSvg(id, name, verifyUrl) {
    const label = xmlEscape((name || "NOTHING").slice(0, 44));
    const safeId = xmlEscape(id);
    const href = htmlEscape(verifyUrl);
    return '<a xmlns="http://www.w3.org/2000/svg" href="' + href + '" target="_blank" rel="noopener noreferrer">' +
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 520 180" role="img" aria-label="NOTHING identity ' + safeId + '">' +
      '<rect x="10" y="10" width="500" height="160" rx="34" fill="#111"/>' +
      '<rect x="18" y="18" width="484" height="144" rx="28" fill="#f7f7f4"/>' +
      '<circle cx="92" cy="90" r="43" fill="#333"/>' +
      '<text x="92" y="102" text-anchor="middle" font-size="48" font-family="Arial,sans-serif" fill="#fff">◇</text>' +
      '<text x="155" y="70" font-family="Arial,sans-serif" font-size="18" font-weight="700" fill="#111">NOTHING</text>' +
      '<text x="155" y="98" font-family="Arial,sans-serif" font-size="17" font-weight="700" fill="#111">' + label + '</text>' +
      '<text x="155" y="124" font-family="monospace" font-size="14" fill="#555">' + safeId + '</text>' +
      '<text x="470" y="139" text-anchor="end" font-family="Arial,sans-serif" font-size="10" fill="#777">CLICK TO VERIFY</text>' +
      '</svg></a>';
  }

  function showResult(identity, verifyUrl) {
    const id = identity.id || identity.nothing_id;
    $("result-id").textContent = id;
    $("result-name").textContent = identity.subject?.name || "—";
    const official = Array.isArray(identity.claims) && identity.claims.some(
      claim => claim?.authorization?.status === "CONFIRMED"
    );
    $("result-copy").textContent = official
      ? "The record includes organization-control authorization evidence; legal/trademark status is not inferred beyond the documented evidence."
      : "The identity is recorded as SELF-CLAIMED unless and until independent verification events are added.";
    $("result").hidden = false;
    const url = verifyUrl || new URL("./verify.html?id=" + encodeURIComponent(id), location.href).href;
    const svg = badgeSvg(id, identity.subject?.name || "", url);
    $("badge-preview").innerHTML = svg;
    $("badge-snippet").value = svg;
    $("hologram").hidden = false;
    $("download-badge").onclick = () => {
      const blob = new Blob([svg], {type:"image/svg+xml;charset=utf-8"});
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = id + "-nothing-badge.svg";
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    };
  }

  async function demo() {
    const d = readDraft();
    const bytes = new TextEncoder().encode(JSON.stringify(d));
    const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    const n = (hash[0] * 65536 + hash[1] * 256 + hash[2]) % 1000000;
    showResult({
      nothing_id: "NTH-" + String(n).padStart(6, "0"),
      subject: {name: d.name, type: d.type}
    });
  }

  $("registrationMode").addEventListener("change", async () => {
    try { await setRegistrationMode(); } catch (e) { officialStatus(e.message); }
  });
  $("get-domain-challenge").addEventListener("click", async () => {
    try { await getDomainChallenge(); } catch (e) { $("domain-state").textContent = e.message; }
  });
  $("verify-domain").addEventListener("click", async () => {
    try { await verifyDomain(); } catch (e) { $("domain-state").textContent = e.message; }
  });
  $("wallet-auth").addEventListener("click", async () => {
    try { await authorizeWithWallet(); } catch (e) { $("wallet-auth-state").textContent = e.message; }
  });

  fillDraft(decodeDraft());
  setRegistrationMode().catch(() => {});
  if (API_BASE && (WALLET_ENABLED || WALLET_AUTH_ENABLED)) {
    discoverWallets();
  } else {
    $("wallet-list").innerHTML =
      '<div class="muted"><strong>Wallet disabled.</strong> The public prototype does not request wallet access while the deployment is under security review.</div>';
    $("wallet-state").textContent = "No wallet connection or transaction is requested on this deployment.";
    $("payment-box").hidden = true;
  }
  loadConfig();
  $("pay").addEventListener("click", async () => {
    try { await pay(); } catch (e) { $("payment-status").textContent = e.message; }
  });
  $("demo").addEventListener("click", async () => {
    try { await demo(); } catch (e) { alert(e.message); }
  });
})();
