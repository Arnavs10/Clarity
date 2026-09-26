// Decide cheaply whether this page is even contract-like before doing anything
// expensive. Three signals, two must fire. Scanning every page would burn API
// budget and, worse, train the user to ignore the badge.

const ACCEPT = /\b(accept|agree|i consent|sign|continue)\b/i;
const LEGAL = ["shall", "hereby", "party", "terminate", "liability",
               "indemnif", "governing law", "covenant", "herein"];
const PATHS = /terms|tos|privacy|agreement|eula|policy|offer|lease|tenancy/i;

function pageText() {
  const clone = document.body.cloneNode(true);
  clone.querySelectorAll("nav,header,footer,script,style,noscript").forEach(n => n.remove());
  return clone.innerText.replace(/\s+/g, " ").trim();
}

function looksLikeContract(text) {
  let score = 0;
  if ([...document.querySelectorAll("button,a,input[type=submit]")]
      .some(el => ACCEPT.test(el.innerText || el.value || ""))) score++;
  const low = text.toLowerCase();
  if (text.split(" ").length > 2000 &&
      LEGAL.filter(w => low.includes(w)).length >= 3) score++;
  if (PATHS.test(location.pathname)) score++;
  return score >= 2;
}

async function sha256(s) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
}

(async () => {
  const { muted = [] } = await chrome.storage.sync.get("muted");
  if (muted.includes(location.hostname)) return;

  const text = pageText();
  if (!looksLikeContract(text)) return;

  chrome.runtime.sendMessage({
    type: "AUDIT",
    text: text.slice(0, 60000),
    hash: await sha256(text),
    host: location.hostname
  });
})();
