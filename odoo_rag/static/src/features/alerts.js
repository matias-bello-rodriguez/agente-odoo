import { dom } from "../dom.js";
import { apiFetchJson } from "../api/http.js";
import { autoResizeComposer } from "../ui/composer.js";

const ALERT_PROMPT = {
  low_stock: "Revisa productos bajo mínimo de stock",
  overdue_invoices: "Muéstrame las facturas vencidas",
  stale_drafts: "Muéstrame las facturas en borrador antiguas",
};

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function renderAlerts(alerts) {
  dom.alertsListEl.innerHTML = "";
  if (!alerts.length) {
    dom.alertsListEl.innerHTML = '<span class="hint subtle">Sin alertas configuradas.</span>';
    return;
  }
  for (const a of alerts) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `alert-item sev-${a.severity || "ok"}`;
    btn.title = a.summary || a.title || "";
    btn.innerHTML = `
      <span class="alert-title">${escapeHtml(a.title || a.id)}</span>
      <span class="alert-badge">${Number(a.count || 0)}</span>`;
    btn.addEventListener("click", () => {
      const prompt = ALERT_PROMPT[a.id];
      if (!prompt) return;
      dom.inputEl.value = prompt;
      autoResizeComposer();
      dom.inputEl.focus();
    });
    dom.alertsListEl.appendChild(btn);
  }
}

export async function loadAlerts({ force = false } = {}) {
  if (!dom.alertsListEl) return;
  dom.alertsListEl.innerHTML = '<span class="hint subtle">Cargando…</span>';
  try {
    const url = force ? "/api/alerts/run" : "/api/alerts";
    const opts = force
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ use_cache: false }) }
      : {};
    const { ok, data } = await apiFetchJson(url, opts);
    if (!ok || !data?.alerts) {
      dom.alertsListEl.innerHTML = '<span class="hint subtle">No se pudo cargar.</span>';
      return;
    }
    renderAlerts(data.alerts);
  } catch (err) {
    dom.alertsListEl.innerHTML = `<span class="hint subtle">${escapeHtml(String(err))}</span>`;
  }
}

export function attachAlerts() {
  if (dom.btnAlertsRefresh) dom.btnAlertsRefresh.addEventListener("click", () => loadAlerts({ force: true }));
  setTimeout(() => loadAlerts({ force: false }), 600);
}

