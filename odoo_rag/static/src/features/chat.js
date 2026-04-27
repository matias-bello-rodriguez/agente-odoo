import { dom } from "../dom.js";
import { apiFetchJson, formatDetail } from "../api/http.js";
import { appendMessage } from "../ui/messages.js";
import { showToast } from "../ui/toast.js";
import { resetComposerHeight, autoResizeComposer } from "../ui/composer.js";
import { openActionModal, confirmActionInsert } from "./actions.js";

/**
 * Ejecuta una consulta al chat como si el usuario la hubiera enviado,
 * sin escribir el texto en el compositor (útil para módulos y atajos).
 */
export async function runChatMessage(message) {
  const text = String(message || "").trim();
  if (!text || !dom.composer) return;

  appendMessage("user", text);

  const loading = appendMessage("assistant", "Pensando…", { loading: true });
  dom.btnSend.disabled = true;

  try {
    const top_k = Number(dom.topKEl?.value) || 6;
    const { ok, data, statusText } = await apiFetchJson("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, top_k }),
    });
    loading.remove();

    if (!ok) {
      appendMessage("assistant", formatDetail(data.detail) || statusText || "Error al consultar.");
      showToast(formatDetail(data.detail) || "Error en la consulta", true);
      return;
    }

    appendMessage("assistant", data.reply || "");

    if (data.draft_action) {
      openActionModal(data.draft_action);
      if (
        data.draft_action.operation === "list" ||
        (data.draft_action.operation === "erp" && data.draft_action.kind === "read")
      ) {
        await confirmActionInsert();
      }
    }
  } catch (err) {
    loading.remove();
    appendMessage("assistant", String(err));
    showToast("No se pudo contactar al servidor.", true);
  } finally {
    dom.btnSend.disabled = false;
    if (dom.inputEl) dom.inputEl.focus();
    autoResizeComposer();
  }
}

export function attachChat() {
  if (!dom.composer || !dom.inputEl) return;

  dom.composer.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = dom.inputEl.value.trim();
    if (!text) return;

    dom.inputEl.value = "";
    resetComposerHeight();

    await runChatMessage(text);
  });
}

