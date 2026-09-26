const esc = s => (s || "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const LABEL = { statutory: "the Act says this", regulatory: "the regulator directs",
                policy: "benchmark, non-binding", norm: "unusual vs comparable" };

(async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const key = "tab_" + tab.id;
  const store = await chrome.storage.local.get(key);
  const data = store[key];
  const body = document.getElementById("body");

  document.getElementById("mute").onclick = async () => {
    const host = new URL(tab.url).hostname;
    const { muted = [] } = await chrome.storage.sync.get("muted");
    if (!muted.includes(host)) {
      await chrome.storage.sync.set({ muted: [...muted, host] });
    }
    document.getElementById("mute").textContent = "Muted";
  };

  if (!data) return;

  if (data.extraction_available === false) {
    body.innerHTML = '<div class="warn"><b>Not analysed.</b> ' +
      esc(data.warning || "No extraction backend configured.") + '</div>';
    return;
  }

  const all = (data.results || []).flatMap(r => r.findings || []);
  if (!all.length) {
    body.innerHTML = '<div class="ok"><b>No rule fired.</b><br>' +
      'Nothing matched the rule set. That is not the same as a lawyer finding nothing.</div>';
    return;
  }

  const high = all.filter(f => f.severity === "high").length;
  body.innerHTML =
    '<div class="sum">' +
      '<div class="chip"><b>' + data.clauses + '</b><span>Clauses</span></div>' +
      '<div class="chip hi"><b>' + high + '</b><span>High</span></div>' +
      '<div class="chip md"><b>' + all.length + '</b><span>Findings</span></div>' +
    '</div>' +
    all.map(f =>
      '<div class="f ' + f.severity + '">' +
        '<span class="tag t-' + f.severity + '">' + f.severity + '</span>' +
        '<span class="tag t-b">' + (LABEL[f.basis] || f.basis) + '</span>' +
        '<div class="m">' + esc(f.message) + '</div>' +
        (f.citation_span
          ? '<details><summary>Show the law</summary><div class="span">' +
            esc(f.citation_span.slice(0, 400)) + '</div></details>'
          : '') +
      '</div>').join("");
})();
