(() => {
  "use strict";
  const API_BASE = String(window.NOTHING_ENROLLMENT_API_BASE || window.NOTHING_API_BASE || "").replace(/\/$/, "");
  const $ = (id) => document.getElementById(id);
  const providers = new Map();
  let selectedProvider = null;
  let walletAddress = null;
  let invoice = null;

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

  async function connect(key) {
    const item = providers.get(key);
    if (!item) return;
    selectedProvider = item.provider;
    const accounts = await selectedProvider.request({method:"eth_requestAccounts"});
    walletAddress = accounts?.[0] || null;
    if (!walletAddress) throw new Error("Wallet returned no account.");
    const chainId = await selectedProvider.request({method:"eth_chainId"});
    $("wallet-state").textContent = walletLabel(item.info) + " · " + walletAddress + " · chain " + chainId;
    if (window.liveConfig?.enabled) await requestQuote(chainId);
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
    if (!API_BASE) {
      $("registration-state").textContent = "PAYMENT GATED";
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

  const atomicHex = (n) => {
    const value = BigInt(String(n));
    if (value <= 0n) throw new Error("Payment amount must be positive.");
    return "0x" + value.toString(16);
  };

  async function pay() {
    if (!selectedProvider || !walletAddress) throw new Error("Connect a wallet first.");
    if (!invoice) throw new Error("Request an invoice first.");
    const chainId = await selectedProvider.request({method:"eth_chainId"});
    if (String(chainId).toLowerCase() !== String(invoice.chain_id).toLowerCase()) {
      throw new Error("Switch the wallet to " + invoice.chain_id + " and try again.");
    }
    const txHash = await selectedProvider.request({
      method:"eth_sendTransaction",
      params:[{
        from: walletAddress,
        to: invoice.recipient,
        value: atomicHex(invoice.amount_atomic)
      }]
    });
    $("payment-status").textContent = "Transaction submitted. NOTHING is verifying it on-chain…";
    const response = await apiJson("/v1/enrollment/complete", {
      method:"POST",
      body:JSON.stringify({
        invoice_id: invoice.invoice_id,
        registration: readDraft(),
        wallet_address: walletAddress,
        tx_hash: txHash
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
    $("result-copy").textContent = "The identity is recorded as SELF-CLAIMED unless and until independent verification events are added.";
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

  fillDraft(decodeDraft());
  discoverWallets();
  loadConfig();
  $("pay").addEventListener("click", async () => {
    try { await pay(); } catch (e) { $("payment-status").textContent = e.message; }
  });
  $("demo").addEventListener("click", async () => {
    try { await demo(); } catch (e) { alert(e.message); }
  });
})();
