// Omni Studio Pro — 24/7 Live Cookie Auto-Sync Service Worker
// Automatically syncs fresh rotated __Secure-1PSIDTS + SAPISID + active Google Vids doc_id to Omni Studio every 4 minutes.

const DEFAULT_ENDPOINTS = [
  "http://127.0.0.1:8765/api/session/extension-sync",
  "https://omni.mcpads.win/api/session/extension-sync"
];

async function findActiveVidsTabInfo() {
  try {
    const tabs = await chrome.tabs.query({ url: "https://docs.google.com/videos/*" });
    for (const t of tabs) {
      const m = (t.url || "").match(/docs\.google\.com\/videos\/(?:u\/(\d+)\/)?d\/([a-zA-Z0-9_-]{25,60})/);
      if (m) {
        return {
          authuser: m[1] || "0",
          doc_id: m[2],
          tab_title: t.title || ""
        };
      }
    }
  } catch (e) {}
  return { authuser: "0", doc_id: "" };
}

async function collectGoogleCookies() {
  const all = await chrome.cookies.getAll({ domain: "google.com" });
  const map = {};
  let sapisid = "";
  for (const c of all) {
    if (!c.name || !c.value) continue;
    if (c.domain && c.domain.includes("play.google.com")) continue;
    if (c.name === "OSID" || c.name === "__Secure-OSID") continue;
    map[c.name] = c.value;
    if (c.name === "SAPISID") sapisid = c.value;
    else if ((c.name === "__Secure-1PAPISID" || c.name === "__Secure-3PAPISID") && !sapisid) {
      sapisid = c.value;
    }
  }
  const cookieStr = Object.entries(map).map(([k, v]) => `${k}=${v}`).join("; ");
  return {
    cookie: cookieStr,
    sapisid,
    has_1psidts: Boolean(map["__Secure-1PSIDTS"])
  };
}

async function syncNow(trigger = "alarm") {
  const { cookie, sapisid, has_1psidts } = await collectGoogleCookies();
  if (!cookie || !sapisid || !has_1psidts) {
    await chrome.storage.local.set({
      lastStatus: "Chưa đăng nhập Google hoặc chưa có __Secure-1PSIDTS",
      lastSyncAt: new Date().toLocaleTimeString("vi-VN")
    });
    return { ok: false, error: "missing_cookies" };
  }

  const stored = await chrome.storage.local.get(["savedDocId", "savedLabel", "savedEndpoint"]);
  const tabInfo = await findActiveVidsTabInfo();
  const docId = tabInfo.doc_id || stored.savedDocId || "";
  if (tabInfo.doc_id) {
    await chrome.storage.local.set({ savedDocId: tabInfo.doc_id });
  }

  const payload = {
    cookie,
    sapisid,
    doc_id: docId,
    authuser: tabInfo.authuser || "0",
    label: stored.savedLabel || `Chrome Auto-Sync (${docId ? docId.slice(0, 8) : sapisid.slice(0, 8)})`,
    trigger
  };

  const endpoints = stored.savedEndpoint ? [stored.savedEndpoint, ...DEFAULT_ENDPOINTS] : DEFAULT_ENDPOINTS;
  for (const ep of endpoints) {
    try {
      const resp = await fetch(ep, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (resp.ok) {
        const data = await resp.json();
        await chrome.storage.local.set({
          lastStatus: `✅ Đồng bộ thành công (${data.doc_id || docId || "active"})`,
          lastSyncAt: new Date().toLocaleTimeString("vi-VN"),
          lastKey: data.key || ""
        });
        return { ok: true, data };
      }
    } catch (err) {}
  }

  await chrome.storage.local.set({
    lastStatus: "⚠️ Không kết nối được máy chủ Omni Studio (port 8765)",
    lastSyncAt: new Date().toLocaleTimeString("vi-VN")
  });
  return { ok: false, error: "unreachable" };
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create("omni_cookie_sync", { periodInMinutes: 4 });
  syncNow("installed");
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create("omni_cookie_sync", { periodInMinutes: 4 });
  syncNow("startup");
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "omni_cookie_sync") {
    syncNow("alarm_4m");
  }
});

// Auto-sync immediately whenever user opens or refreshes a Google Vids tab
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === "complete" && tab.url && tab.url.includes("docs.google.com/videos/")) {
    syncNow("vids_tab_loaded");
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "SYNC_NOW") {
    syncNow("manual_click").then(sendResponse);
    return true;
  }
});
