import { apiFetchJson } from "./http.js";
import { dom } from "../dom.js";
import { state } from "../state.js";
import { showToast } from "../ui/toast.js";

export async function refreshHealth() {
  try {
    const { ok, data } = await apiFetchJson("/api/health");
    dom.healthPill.textContent = ok && data.ok ? "En línea" : "Error";
    dom.healthPill.classList.toggle("warn", !(ok && data.ok));

    state.odooBaseUrl = String(data.odoo_url || "").replace(/\/+$/, "");
    dom.odooTarget.textContent = `${data.odoo_url} · ${data.odoo_db}`;
    dom.indexState.textContent = data.indexed ? "Listo" : "Sin índice";
    dom.indexState.classList.toggle("dead", !data.indexed);
    if (!data.openai_configured) showToast("Falta OPENAI_API_KEY en .env", true);
  } catch {
    dom.healthPill.textContent = "Sin conexión";
    dom.healthPill.classList.add("warn");
  }
}

