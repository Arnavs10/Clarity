# Roadmap

What is not built, with enough detail to pick up cold. Written so this can be handed
to another engineer, another AI, or future you, without needing the conversation
that produced it.

---

## v2: browser extension

The strongest single addition. Today someone has to think to upload a contract. The
extension removes that step.

### Shape

```
content script   pulls contract or terms text out of the page DOM
service worker   holds the cache, calls the backend, sets the badge
popup            shows findings, links to the full report
backend          the FastAPI wrapper around src/agent.py. No new backend needed
```

Manifest V3. The extension is a client of what already exists.

### manifest.json

```json
{
  "manifest_version": 3,
  "name": "Clarity",
  "version": "0.1.0",
  "permissions": ["activeTab", "storage", "scripting"],
  "host_permissions": ["https://your-backend.example.com/*"],
  "background": { "service_worker": "worker.js" },
  "action": { "default_popup": "popup.html" },
  "content_scripts": [{
    "matches": ["<all_urls>"],
    "js": ["content.js"],
    "run_at": "document_idle"
  }]
}
```

### Content script

Detect a contract-like page before doing anything expensive. Cheap signals, in order:

1. A `<form>` or button whose text matches `/accept|agree|i consent|sign/i`
2. Page text over ~2000 words containing three or more of: `shall`, `hereby`,
   `party`, `terminate`, `liability`, `indemnif`, `governing law`
3. URL path matching `/terms|tos|privacy|agreement|eula|policy/`

Two of the three should fire before you extract. Extract with
`document.body.innerText`, strip nav and footer by dropping nodes inside `<nav>`,
`<header>`, `<footer>`, then post to the backend.

### The false-alarm problem

This is the part that decides whether the extension is useful or gets uninstalled on
day two. Interrupting someone over a clause that is not actually a problem is worse
than staying quiet.

Three gates, all must pass before anything visible happens:

1. **Basis gate.** Only `statutory` and `policy` findings can raise a prompt. `norm`
   findings are visible in the popup but never interrupt. A market convention is not
   worth stopping someone over.
2. **Verification gate.** Only findings where `verified == true`. The backend already
   enforces this; the extension re-checks rather than trusting the payload.
3. **Severity gate.** Only `high`. Medium and low sit in the popup.

Badge behaviour: a count for anything found, colour only when a gate-passing finding
exists. Never a modal, never a full-page overlay. The badge is the interruption.

Add a per-domain mute stored in `chrome.storage.sync`, and respect it forever.

### Caching

Hash the extracted text with SHA-256 and store `{hash: {findings, ts}}` in
`chrome.storage.local`. Skip the backend call when the hash matches and the entry is
under 30 days old. Terms pages change rarely and re-scanning every visit burns both
API budget and the user's patience.

Cap the store at ~200 entries, evict oldest first.

### Backend endpoint

`src/api.py` needs one route the extension calls:

```
POST /audit
body: { "text": "...", "doc_type": "terms" | "employment" | "rental" }
returns: { "findings": [...], "clauses": n, "cached": false }
```

Wrap `ClauseAgent.run` over segmented clauses, exactly as `app.py` does. Rate limit
per install id. Do not log the contract text: people paste employment terms into
this, and storing them is a liability with no upside.

### Steps, in order

1. Wrap `src/agent.py` in FastAPI as `src/api.py`, deploy it, confirm with curl
2. Scaffold the extension, load unpacked, get the content script logging page text
3. Wire the backend call, render findings in the popup
4. Add the three gates and the badge
5. Add caching and per-domain mute
6. A `terms` document type: one profile in `config/types/`, one rule file in
   `config/rules/`. Consumer Protection Act 2019 unfair-terms provisions are the
   grounding to research
7. Web Store submission: privacy policy, screenshots, justify each permission

Rough effort: 2 to 3 days for steps 1 to 5, plus whatever the `terms` grounding
research takes.

---

## Other unbuilt work

**A fourth document type.** NDAs are the cheapest add: they share the Contract Act
corpus with employment, and confidentiality, IP and non-solicit are already in that
taxonomy. A profile, a rule file, and a labeled set.

**Dense retrieval.** `src/retrieve.py` is BM25 behind an interface. Adding embeddings
means one class with the same two methods and a merge step. Worth it once the corpus
grows past a handful of Acts.

**React frontend.** `app.py` is Streamlit for speed. The split-screen reader layout,
severity-only colour, and expandable statute spans all transfer directly.

**Case law tier.** The rule files have three basis tiers. A fourth, `precedent`,
would need a real case-law source and per-rule citations. Deliberately not attempted:
an unsourced precedent is worse than no rule.

**Regional languages.** Rental agreements are often bilingual. Segmentation and
extraction would need a language detection step first.
