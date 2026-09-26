// Cache, call the backend, set the badge. The three gates that decide whether the
// user is interrupted live here, not in the backend, so the policy is visible and
// editable in one place.

const API = "http://localhost:8000";
const MAX_CACHE = 200;
const TTL_DAYS = 30;

// Only a finding that is statutory or policy AND verified AND high severity may
// raise a visible alert. Norm-tier findings are market conventions, not law, and
// interrupting someone over a convention is how an extension gets uninstalled.
function alertable(f) {
  return ["statutory", "regulatory", "policy"].includes(f.basis)
      && f.verified === true
      && f.severity === "high";
}

async function cached(hash) {
  const { cache = {} } = await chrome.storage.local.get("cache");
  const hit = cache[hash];
  if (!hit) return null;
  if (Date.now() - hit.ts > TTL_DAYS * 864e5) return null;
  return hit.data;
}

async function store(hash, data) {
  let { cache = {} } = await chrome.storage.local.get("cache");
  cache[hash] = { data, ts: Date.now() };
  const keys = Object.keys(cache);
  if (keys.length > MAX_CACHE) {
    keys.sort((a, b) => cache[a].ts - cache[b].ts)
        .slice(0, keys.length - MAX_CACHE)
        .forEach(k => delete cache[k]);
  }
  await chrome.storage.local.set({ cache });
}

function badge(tabId, data) {
  const all = (data.results || []).flatMap(r => r.findings || []);
  const loud = all.filter(alertable).length;
  chrome.action.setBadgeText({ tabId, text: all.length ? String(all.length) : "" });
  chrome.action.setBadgeBackgroundColor({
    tabId, color: loud ? "#C4362B" : "#F5C518"
  });
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type !== "AUDIT") return;

  (async () => {
    let data = await cached(msg.hash);
    if (!data) {
      try {
        const res = await fetch(`${API}/audit`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: msg.text, doc_type: "employment" })
        });
        if (!res.ok) return;
        data = await res.json();
        await store(msg.hash, data);
      } catch (e) { return; }
    }
    await chrome.storage.local.set({ ["tab_" + sender.tab.id]: data });
    badge(sender.tab.id, data);
  })();

  return true;
});
