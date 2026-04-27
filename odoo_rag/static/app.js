import { dom } from "./src/dom.js";
import { apiFetchJson, formatDetail } from "./src/api/http.js";
import { showToast } from "./src/ui/toast.js";
import { attachComposerUX, resetComposerHeight } from "./src/ui/composer.js";
import { refreshHealth } from "./src/api/health.js";
import { attachActionModalHandlers } from "./src/features/actions.js";
import { attachChat } from "./src/features/chat.js";
import { attachVoice } from "./src/features/voice.js";
import { attachAlerts } from "./src/features/alerts.js";
import { attachReportButton } from "./src/features/report.js";
import { attachJoule } from "./src/features/joule.js";
import { attachPrompts } from "./src/features/prompts.js";

async function rebuildIndex() {
  const ok = window.confirm("¿Reindexar desde Odoo? Puede tardar varios minutos y consumir cuota de OpenAI.");
  if (!ok) return;
  dom.btnRebuild.disabled = true;
  showToast("Reindexando… no cierres esta pestaña.");
  try {
    const { ok: ok2, data } = await apiFetchJson("/api/index/rebuild", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    });
    if (!ok2) {
      showToast(formatDetail(data.detail) || "Falló la reindexación", true);
      return;
    }
    showToast("Índice actualizado.");
    await refreshHealth();
  } catch (err) {
    showToast(String(err), true);
  } finally {
    dom.btnRebuild.disabled = false;
  }
}

dom.btnClear.addEventListener("click", () => {
  dom.messagesEl.innerHTML = "";
});

dom.btnRebuild.addEventListener("click", rebuildIndex);

refreshHealth();
attachComposerUX();
resetComposerHeight();
attachActionModalHandlers();
attachChat();
attachVoice();
attachAlerts();
attachReportButton();
attachJoule();
attachPrompts();
dom.inputEl.focus();
