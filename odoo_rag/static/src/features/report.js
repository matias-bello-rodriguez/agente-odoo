import { dom } from "../dom.js";
import { apiFetchJson, formatDetail } from "../api/http.js";
import { showToast } from "../ui/toast.js";
import { appendMessage } from "../ui/messages.js";

function fmtMoney(value) {
  const n = Number(value || 0);
  if (!isFinite(n)) return "0";
  return n.toLocaleString("es-CL", { maximumFractionDigits: 0 });
}

function renderReport(payload) {
  const totals = payload?.data?.totals || {};
  const period = payload?.data?.period || {};
  const top = payload?.data?.top_customers || [];
  const lines = [];
  lines.push(`Reporte de ventas (${period.label || "mes"})`);
  lines.push(
    `• Ventas: ${fmtMoney(totals.sales_amount)}  |  Mes anterior: ${fmtMoney(totals.previous_month_sales)}` +
      (totals.growth_pct != null ? `  |  Crec.: ${totals.growth_pct.toFixed(2)}%` : "")
  );
  lines.push(`• Facturado: ${fmtMoney(totals.invoiced_amount)}  |  Órdenes confirmadas: ${totals.confirmed_orders ?? 0}`);
  if (top.length) {
    lines.push("• Top clientes:");
    for (const c of top.slice(0, 5)) lines.push(`   - ${c.name}: ${fmtMoney(c.amount)}`);
  }
  if (payload?.summary) {
    lines.push("\nAnálisis:");
    lines.push(payload.summary);
  }
  appendMessage("assistant", lines.join("\n"));
}

export function attachReportButton() {
  if (!dom.btnReport) return;
  dom.btnReport.addEventListener("click", async () => {
    dom.btnReport.disabled = true;
    const originalLabel = dom.btnReport.textContent;
    dom.btnReport.textContent = "Generando reporte…";
    try {
      const { ok, data } = await apiFetchJson("/api/report/sales", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ write_summary: true }),
      });
      if (!ok) {
        showToast(formatDetail(data?.detail) || "No se pudo generar el reporte.", true);
        return;
      }
      renderReport(data);
    } catch (err) {
      showToast(String(err), true);
    } finally {
      dom.btnReport.disabled = false;
      dom.btnReport.textContent = originalLabel;
    }
  });
}

