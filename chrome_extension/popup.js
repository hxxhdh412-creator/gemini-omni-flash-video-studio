async function loadState() {
  const st = await chrome.storage.local.get(["lastStatus", "lastSyncAt", "savedLabel", "savedDocId"]);
  document.getElementById("labelInput").value = st.savedLabel || "";
  document.getElementById("statusBox").textContent =
    `${st.lastStatus || "Chưa đồng bộ"}\nLần cuối: ${st.lastSyncAt || "—"}\nDoc ID: ${st.savedDocId || "(Tự nhận khi mở vids.new)"}`;
}

document.getElementById("syncBtn").addEventListener("click", async () => {
  const lbl = document.getElementById("labelInput").value.trim();
  await chrome.storage.local.set({ savedLabel: lbl });
  document.getElementById("statusBox").textContent = "Đang đồng bộ cookie tươi…";
  await chrome.runtime.sendMessage({ type: "SYNC_NOW" });
  await loadState();
});

loadState();
