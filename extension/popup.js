(() => {
  "use strict";
  const api = globalThis.browser ?? globalThis.chrome;
  const DEFAULTS = {
    registrationUrl: "https://YOUR-NOTHING-DOMAIN.example/register.html",
    verifyUrl: "https://YOUR-NOTHING-DOMAIN.example/verify.html",
    docsUrl: "https://github.com/asnad68/NOTHING"
  };
  const $ = (id) => document.getElementById(id);

  async function getConfig() {
    const stored = await api.storage.local.get(DEFAULTS);
    return {...DEFAULTS, ...stored};
  }

  function normalizeUrl(value) {
    const v = String(value || "").trim();
    if (!v) return "";
    try { return new URL(v).href; } catch { return v; }
  }

  function splitLines(value) {
    return String(value || "").split(/[\n,]+/).map(s => s.trim()).filter(Boolean);
  }

  function encodeFragment(obj) {
    const bytes = new TextEncoder().encode(JSON.stringify(obj));
    let binary = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
  }

  async function currentTab() {
    const tabs = await api.tabs.query({active: true, currentWindow: true});
    return tabs[0] || null;
  }

  async function inspectTab() {
    const tab = await currentTab();
    if (!tab?.id) throw new Error("The active page could not be inspected.");
    const results = await api.scripting.executeScript({
      target: {tabId: tab.id},
      func: () => {
        const canonical = document.querySelector('link[rel="canonical"]')?.href || "";
        const ogSite = document.querySelector('meta[property="og:site_name"]')?.content || "";
        const description = document.querySelector('meta[name="description"]')?.content || "";
        return {title: document.title, href: location.href, host: location.hostname, canonical, ogSite, description};
      }
    });
    return results?.[0]?.result || {};
  }

  function applyDetected(data) {
    const inferred = data.ogSite || data.title || data.host || "";
    if (!$('name').value.trim()) $('name').value = inferred.slice(0, 180);
    if (!$('website').value.trim()) $('website').value = normalizeUrl(data.canonical || data.href || "");
    if (!$('domains').value.trim() && data.host) $('domains').value = data.host;
    $('detected').hidden = false;
    $('detected').innerHTML = `<strong>${escapeHtml(inferred || "Page detected")}</strong><div>${escapeHtml(data.href || "")}</div>`;
  }

  function escapeHtml(value) {
    return String(value ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#039;");
  }

  async function loadDraft() {
    const {registrationDraft} = await api.storage.local.get({registrationDraft: null});
    if (!registrationDraft) return;
    $('name').value = registrationDraft.name || "";
    $('website').value = registrationDraft.website || "";
    $('domains').value = (registrationDraft.domains || []).join("\n");
    $('channels').value = (registrationDraft.channels || []).join("\n");
  }

  function readDraft() {
    const name = $('name').value.trim();
    if (!name) throw new Error("Enter the business or brand name first.");
    return {
      name,
      type: "business",
      website: normalizeUrl($('website').value),
      domains: splitLines($('domains').value),
      channels: splitLines($('channels').value),
      created_from: "nothing-browser-extension",
      captured_at: new Date().toISOString()
    };
  }

  async function saveDraft() {
    const draft = readDraft();
    await api.storage.local.set({registrationDraft: draft});
    $('message').textContent = "Draft saved locally in the browser.";
    $('message').className = "message ok";
  }

  async function openRegistration() {
    const draft = readDraft();
    const cfg = await getConfig();
    if (cfg.registrationUrl.includes("YOUR-NOTHING-DOMAIN")) {
      throw new Error("Set the real NOTHING registration URL in Settings first.");
    }
    await api.storage.local.set({registrationDraft: draft});
    const url = cfg.registrationUrl + "#draft=" + encodeFragment(draft);
    await api.tabs.create({url});
  }

  async function openVerify() {
    const id = $('nothingId').value.trim().toUpperCase();
    if (!/^NTH-[0-9]{6}$/.test(id)) throw new Error("Use a Nothing ID such as NTH-000001.");
    const cfg = await getConfig();
    if (cfg.verifyUrl.includes("YOUR-NOTHING-DOMAIN")) throw new Error("Set the real NOTHING verification URL in Settings first.");
    await api.tabs.create({url: cfg.verifyUrl + "?id=" + encodeURIComponent(id)});
  }

  async function init() {
    await loadDraft();
    $('detect').addEventListener('click', async () => {
      try { applyDetected(await inspectTab()); $('message').textContent = "Current page inspected."; $('message').className = "message ok"; }
      catch (e) { $('message').textContent = e.message; $('message').className = "message error"; }
    });
    $('save').addEventListener('click', async () => { try { await saveDraft(); } catch (e) { $('message').textContent=e.message; $('message').className="message error"; } });
    $('register').addEventListener('click', async () => { try { await openRegistration(); } catch (e) { $('message').textContent=e.message; $('message').className="message error"; } });
    $('verify').addEventListener('click', async () => { try { await openVerify(); } catch (e) { $('message').textContent=e.message; $('message').className="message error"; } });
    $('settings').addEventListener('click', () => api.runtime.openOptionsPage());
    $('help').addEventListener('click', async (event) => { event.preventDefault(); const cfg=await getConfig(); await api.tabs.create({url:cfg.docsUrl}); });
  }
  init();
})();
