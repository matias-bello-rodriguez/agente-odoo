import { dom } from "../dom.js";
import { autoResizeComposer } from "../ui/composer.js";

const TRIGGER_PATTERNS = [
  { kind: "partner", regex: /(?:cliente|para|a)\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 ._-]{2,40})$/i },
  { kind: "vendor", regex: /(?:proveedor|al proveedor)\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 ._-]{2,40})$/i },
  { kind: "product", regex: /(?:producto|item|artículo|articulo|sku)\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 ._/-]{2,40})$/i },
];

let activeIndex = -1;
let currentItems = [];
let currentMatch = null;
let lastQueryAbort = null;
let debounceTimer = null;

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function detectTrigger(text) {
  if (!text) return null;
  for (const t of TRIGGER_PATTERNS) {
    const m = text.match(t.regex);
    if (m && m[1] && m[1].trim().length >= 2) return { kind: t.kind, query: m[1].trim(), matched: m[0] };
  }
  return null;
}

async function fetchSuggestions(kind, query) {
  if (lastQueryAbort) lastQueryAbort.abort();
  lastQueryAbort = new AbortController();
  const url = `/api/suggest?kind=${encodeURIComponent(kind)}&q=${encodeURIComponent(query)}&limit=6`;
  try {
    const res = await fetch(url, { signal: lastQueryAbort.signal });
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data?.items) ? data.items : [];
  } catch {
    return [];
  }
}

function hideSuggestions() {
  if (!dom.suggestionsEl) return;
  dom.suggestionsEl.hidden = true;
  dom.suggestionsEl.innerHTML = "";
  activeIndex = -1;
  currentItems = [];
  currentMatch = null;
}

function highlightActive() {
  if (!dom.suggestionsEl) return;
  const nodes = dom.suggestionsEl.querySelectorAll(".suggestion");
  nodes.forEach((n, i) => n.classList.toggle("active", i === activeIndex));
}

function applySuggestion(idx) {
  if (!currentMatch || idx < 0 || idx >= currentItems.length) return hideSuggestions();
  const choice = currentItems[idx];
  const text = dom.inputEl.value;
  const start = text.length - currentMatch.matched.length;
  if (start < 0) return hideSuggestions();
  const prefix = text.slice(0, start);
  const verbMatch = currentMatch.matched.match(/^(\S+)\s+/);
  const verb = verbMatch ? verbMatch[1] + " " : "";
  dom.inputEl.value = prefix + verb + choice.label + " ";
  autoResizeComposer();
  dom.inputEl.focus();
  hideSuggestions();
}

function renderSuggestions(items, kind) {
  if (!dom.suggestionsEl) return;
  if (!items.length) return hideSuggestions();
  activeIndex = -1;
  currentItems = items;
  dom.suggestionsEl.innerHTML = "";
  const kindLabel = { partner: "Cliente", vendor: "Proveedor", product: "Producto" }[kind] || kind;
  items.forEach((it, idx) => {
    const node = document.createElement("div");
    node.className = "suggestion";
    node.setAttribute("role", "option");
    node.dataset.index = String(idx);
    node.innerHTML = `
      <span class="sug-kind">${escapeHtml(kindLabel)}</span>
      <span class="sug-title">${escapeHtml(it.label || "")}</span>
      ${it.subtitle ? `<span class="sug-sub">${escapeHtml(it.subtitle)}</span>` : ""}`;
    node.addEventListener("mousedown", (ev) => {
      ev.preventDefault();
      applySuggestion(idx);
    });
    dom.suggestionsEl.appendChild(node);
  });
  dom.suggestionsEl.hidden = false;
}

export function attachJoule() {
  if (!dom.inputEl) return;

  dom.inputEl.addEventListener("input", () => {
    const match = detectTrigger(dom.inputEl.value);
    if (!match) return hideSuggestions();
    currentMatch = match;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(async () => {
      const items = await fetchSuggestions(match.kind, match.query);
      if (currentMatch === match) renderSuggestions(items, match.kind);
    }, 180);
  });

  dom.inputEl.addEventListener("keydown", (ev) => {
    if (dom.suggestionsEl?.hidden) return;
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      activeIndex = Math.min(currentItems.length - 1, activeIndex + 1);
      highlightActive();
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      activeIndex = Math.max(0, activeIndex - 1);
      highlightActive();
    } else if (ev.key === "Enter" && activeIndex >= 0) {
      ev.preventDefault();
      applySuggestion(activeIndex);
    } else if (ev.key === "Escape") {
      hideSuggestions();
    } else if (ev.key === "Tab" && currentItems.length) {
      ev.preventDefault();
      applySuggestion(activeIndex >= 0 ? activeIndex : 0);
    }
  });

  dom.inputEl.addEventListener("blur", () => setTimeout(hideSuggestions, 120));
}

