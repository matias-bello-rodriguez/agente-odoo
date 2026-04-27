import { dom } from "../dom.js";
import { state } from "../state.js";
import { apiFetchJson, formatDetail } from "../api/http.js";
import { appendMessage } from "../ui/messages.js";
import { showToast } from "../ui/toast.js";
import { refreshHealth } from "../api/health.js";

const FIELD_LABELS = {
  name: "Nombre",
  email: "Correo electrónico",
  phone: "Teléfono",
  street: "Dirección",
  city: "Ciudad",
  zip: "Código postal",
  vat: "NIF / VAT",
  is_company: "Es empresa",
  comment: "Notas internas",
  default_code: "Referencia interna",
  list_price: "Precio de venta (PVP)",
  standard_price: "Costo",
  type: "Tipo de producto",
  partner_name: "Cliente",
  invoice_line_name: "Detalle de la línea",
  invoice_line_price_unit: "Monto línea",
  invoice_date: "Fecha de factura",
  invoice_date_due: "Vencimiento",
  ref: "Referencia",
  narration: "Notas de factura",
  move_kind: "Tipo de factura",
  invoice_line_qty: "Cantidad línea factura",
  order_line_name: "Detalle de línea",
  order_line_qty: "Cantidad",
  order_line_price_unit: "Precio unitario",
  order_line_discount: "Descuento (%)",
  client_order_ref: "Referencia cliente",
  note: "Notas",
  vendor_name: "Proveedor",
  partner_ref: "Referencia proveedor",
  notes: "Notas de compra",
  picking_type_code: "Tipo de operación stock",
  origin: "Origen",
  move_line_name: "Detalle movimiento",
  product_name: "Producto",
  move_line_qty: "Cantidad movimiento",
};

function labelForField(key) {
  return FIELD_LABELS[key] || key;
}

function odooRecordUrl(model, recordId) {
  const id = Number(recordId || 0);
  if (!model || !id) return "";
  const base = state.odooBaseUrl || "";
  if (!base) return "";
  return `${base}/web#id=${id}&model=${encodeURIComponent(model)}&view_type=form`;
}

function modelForListQuery(query) {
  if (query === "users_roles" || query === "users_last_login") return "res.users";
  if (query === "accounting_recent_actions" || query === "accounting_missing_key_data") return "account.move";
  if (query === "latest_product") return "product.product";
  if (query === "customers_drop_with_active_contracts") return "res.partner";
  if (query === "delivery_orders") return "sale.order";
  return "";
}

function idFieldForListQuery(query) {
  if (query === "customers_drop_with_active_contracts") return "partner_id";
  return "id";
}

function attachListRowLinks({ query, items, tbody }) {
  const model = modelForListQuery(query);
  if (!model || !Array.isArray(items) || !tbody) return;
  const idField = idFieldForListQuery(query);
  const rows = Array.from(tbody.querySelectorAll("tr"));
  rows.forEach((tr, idx) => {
    const item = items[idx] || {};
    const recId = Number(item[idField] || 0);
    const url = odooRecordUrl(model, recId);
    if (!url) return;
    tr.classList.add("list-row-link");
    tr.title = "Abrir registro en Odoo";
    tr.addEventListener("click", () => window.open(url, "_blank", "noopener,noreferrer"));
  });
}

function escapeModalHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => {
    const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    return map[c] || c;
  });
}

function estimateActionImpact(draft) {
  const out = {
    title: "Impacto estimado",
    summary: "Acción no clasificada.",
    records: "No determinado",
    risk: "Medio",
    reversible: "Parcial",
    bullets: [],
  };
  if (!draft || !draft.operation) return out;
  const op = String(draft.operation);
  if (op === "create") {
    out.summary = `Se creará 1 registro nuevo en ${draft.model || "Odoo"}.`;
    out.records = "1 registro nuevo";
    out.risk = "Medio";
    out.reversible = "Parcial";
    out.bullets = ["No modifica registros existentes.", "Puedes archivarlo o editarlo luego según modelo."];
    return out;
  }
  if (op === "erp") {
    const kind = String(draft.kind || "").toLowerCase();
    const spec = draft.spec || {};
    if (kind === "read") {
      const lim = Number(spec.limit || 0);
      out.summary = `Consulta de datos en ${spec.model || "Odoo"} sin cambios.`;
      out.records = lim > 0 ? `Hasta ${lim} filas` : "Sin límite explícito";
      out.risk = "Bajo";
      out.reversible = "Sí (solo lectura)";
      out.bullets = ["No persiste cambios.", "Solo muestra resultados para análisis."];
      return out;
    }
    if (kind === "write") {
      const vals = spec.values || {};
      const nFields = Object.keys(vals).length;
      out.summary = `Actualizará el registro ${spec.record_id || "?"} en ${spec.model || "Odoo"}.`;
      out.records = "1 registro";
      out.risk = "Medio";
      out.reversible = "Parcial";
      out.bullets = [`Campos estimados a cambiar: ${nFields}.`, "Requiere validar datos antes de confirmar."];
      return out;
    }
    if (kind === "archive") {
      const ids = Array.isArray(spec.record_ids) ? spec.record_ids : [];
      out.summary = `Archivará (desactivará) registros en ${spec.model || "Odoo"}.`;
      out.records = `${ids.length} registro(s)`;
      out.risk = "Medio";
      out.reversible = "Sí (reactivando active=true)";
      out.bullets = ["No borra físicamente los registros.", "Puede afectar listados y reportes por estado activo."];
      return out;
    }
    if (kind === "unlink") {
      const ids = Array.isArray(spec.record_ids) ? spec.record_ids : [];
      out.summary = `Eliminará definitivamente registros en ${spec.model || "Odoo"}.`;
      out.records = `${ids.length} registro(s)`;
      out.risk = "Alto";
      out.reversible = "No";
      out.bullets = ["Borrado físico (hard delete).", "Puede romper trazabilidad y referencias históricas."];
      return out;
    }
    return out;
  }
  if (op === "list") {
    out.summary = "Consulta analítica/listado sin escritura en Odoo.";
    out.records = "Variable (según query)";
    out.risk = "Bajo";
    out.reversible = "Sí (solo lectura)";
    out.bullets = ["No modifica datos del ERP.", "Puede consumir tiempo si la consulta es grande."];
    return out;
  }
  if (op === "email") {
    out.summary = "Enviará un correo desde Odoo.";
    out.records = "1 mensaje";
    out.risk = "Medio";
    out.reversible = "No (si ya fue entregado)";
    out.bullets = ["Revisar destinatario y asunto antes de confirmar.", "Puede generar comunicación externa inmediata."];
    return out;
  }
  if (op === "workflow") {
    out.summary = "Ejecutará un flujo encadenado de múltiples pasos.";
    out.records = "Múltiples registros";
    out.risk = "Alto";
    out.reversible = "Parcial";
    out.bullets = [
      "Puede crear cliente, venta, factura y/o movimientos.",
      "Si un paso falla, algunos pasos previos pueden quedar aplicados.",
    ];
    return out;
  }
  if (op === "product_setup") {
    out.summary = "Creará o configurará producto y reglas relacionadas.";
    out.records = "Múltiples registros técnicos";
    out.risk = "Medio";
    out.reversible = "Parcial";
    out.bullets = ["Impacta inventario, reposición y compras.", "Revisar parámetros de stock mínimo y lead times."];
    return out;
  }
  return out;
}

function impactRiskClass(risk) {
  const r = String(risk || "").toLowerCase();
  if (r === "alto") return "risk-high";
  if (r === "bajo") return "risk-low";
  return "risk-medium";
}

function buildImpactCard(draft) {
  const est = estimateActionImpact(draft);
  const card = document.createElement("section");
  card.className = `modal-impact-card ${impactRiskClass(est.risk)}`;
  const bullets = Array.isArray(est.bullets) ? est.bullets : [];
  card.innerHTML = `
    <div class="modal-impact-head">
      <strong>${escapeModalHtml(est.title)}</strong>
      <span class="modal-impact-risk">Riesgo: ${escapeModalHtml(est.risk)}</span>
    </div>
    <p class="modal-impact-summary">${escapeModalHtml(est.summary)}</p>
    <div class="modal-impact-grid">
      <div><span>Registros</span><b>${escapeModalHtml(est.records)}</b></div>
      <div><span>Reversible</span><b>${escapeModalHtml(est.reversible)}</b></div>
    </div>
    ${
      bullets.length
        ? `<ul class="modal-impact-list">${bullets.map((it) => `<li>${escapeModalHtml(it)}</li>`).join("")}</ul>`
        : ""
    }
  `;
  return card;
}

function buildModalFields(model, values) {
  dom.actionModalForm.innerHTML = "";
  const keys = Object.keys(values).sort((a, b) => a.localeCompare(b));
  for (const key of keys) {
    const val = values[key];
    const wrap = document.createElement("div");
    wrap.className = "modal-field";

    if (key === "is_company") {
      wrap.classList.add("modal-field-row");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.id = `fld-${key}`;
      cb.dataset.field = key;
      cb.checked = Boolean(val);
      const lbl = document.createElement("label");
      lbl.htmlFor = cb.id;
      lbl.textContent = labelForField(key);
      wrap.appendChild(cb);
      wrap.appendChild(lbl);
      dom.actionModalForm.appendChild(wrap);
      continue;
    }

    const lbl = document.createElement("span");
    lbl.className = "label";
    lbl.textContent = labelForField(key);
    wrap.appendChild(lbl);

    if (key === "comment" || key === "narration" || key === "note" || key === "notes") {
      const ta = document.createElement("textarea");
      ta.dataset.field = key;
      ta.value = val == null ? "" : String(val);
      wrap.appendChild(ta);
      dom.actionModalForm.appendChild(wrap);
      continue;
    }

    if (key === "type" && model === "product.product") {
      const sel = document.createElement("select");
      sel.dataset.field = key;
      const opts = [
        ["consu", "Bienes / material (consu)"],
        ["service", "Servicio"],
        ["combo", "Combo"],
      ];
      let cur = String(val || "consu").toLowerCase();
      if (cur === "product") cur = "consu";
      for (const [v, t] of opts) {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = t;
        sel.appendChild(o);
      }
      if (!opts.some((x) => x[0] === cur)) cur = "consu";
      sel.value = cur;
      wrap.appendChild(sel);
      dom.actionModalForm.appendChild(wrap);
      continue;
    }

    if (key === "move_kind" && model === "account.move") {
      const sel = document.createElement("select");
      sel.dataset.field = key;
      const opts = [
        ["out_invoice", "Factura cliente (out_invoice)"],
        ["in_invoice", "Factura proveedor (in_invoice)"],
      ];
      let cur = String(val || "out_invoice").toLowerCase();
      for (const [v, t] of opts) {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = t;
        sel.appendChild(o);
      }
      if (!opts.some((x) => x[0] === cur)) cur = "out_invoice";
      sel.value = cur;
      wrap.appendChild(sel);
      dom.actionModalForm.appendChild(wrap);
      continue;
    }

    if (key === "picking_type_code" && model === "stock.picking") {
      const sel = document.createElement("select");
      sel.dataset.field = key;
      const opts = [
        ["incoming", "Entrada (incoming)"],
        ["outgoing", "Salida (outgoing)"],
        ["internal", "Transferencia interna (internal)"],
      ];
      let cur = String(val || "internal").toLowerCase();
      for (const [v, t] of opts) {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = t;
        sel.appendChild(o);
      }
      if (!opts.some((x) => x[0] === cur)) cur = "internal";
      sel.value = cur;
      wrap.appendChild(sel);
      dom.actionModalForm.appendChild(wrap);
      continue;
    }

    const inp = document.createElement("input");
    inp.dataset.field = key;
    const numeric = new Set([
      "list_price",
      "standard_price",
      "invoice_line_price_unit",
      "invoice_line_qty",
      "order_line_qty",
      "order_line_price_unit",
      "order_line_discount",
      "move_line_qty",
    ]);
    if (numeric.has(key)) {
      inp.type = "number";
      inp.step = "0.01";
      inp.min = "0";
      inp.value = val == null || val === "" ? "" : Number(val);
    } else {
      inp.type = "text";
      inp.value = val == null ? "" : String(val);
    }
    wrap.appendChild(inp);
    dom.actionModalForm.appendChild(wrap);
  }
}

function gatherModalValues() {
  const out = {};
  dom.actionModalForm.querySelectorAll("[data-field]").forEach((el) => {
    const key = el.dataset.field;
    if (!key) return;
    if (el.type === "checkbox") {
      out[key] = el.checked;
      return;
    }
    if (el.tagName === "SELECT") {
      out[key] = el.value;
      return;
    }
    let v = el.value.trim();
    if (el.type === "number") {
      out[key] = v === "" ? "" : Number(v);
      return;
    }
    out[key] = v;
  });
  return out;
}

function closeActionModal() {
  state.pendingActionDraft = null;
  dom.actionModal.hidden = true;
  dom.actionModalForm.innerHTML = "";
  document.body.style.overflow = "";
}

function buildEmailFields(draft) {
  dom.actionModalForm.innerHTML = "";
  const p = (draft && draft.params) || {};
  const targetMap = {
    partner: "Contacto",
    invoice: "Factura",
    sale_order: "Orden de venta",
    purchase_order: "Orden de compra",
  };
  const targetWrap = document.createElement("div");
  targetWrap.className = "modal-field";
  const targetLbl = document.createElement("span");
  targetLbl.className = "label";
  targetLbl.textContent = "Tipo de destinatario";
  const targetSel = document.createElement("select");
  targetSel.dataset.field = "_target";
  for (const [v, t] of Object.entries(targetMap)) {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = t;
    if (v === draft.target) o.selected = true;
    targetSel.appendChild(o);
  }
  targetWrap.appendChild(targetLbl);
  targetWrap.appendChild(targetSel);
  dom.actionModalForm.appendChild(targetWrap);

  function addInput(field, label, value, opts = {}) {
    const wrap = document.createElement("div");
    wrap.className = "modal-field";
    const l = document.createElement("span");
    l.className = "label";
    l.textContent = label;
    const inp = opts.textarea ? document.createElement("textarea") : document.createElement("input");
    if (!opts.textarea) inp.type = opts.type || "text";
    inp.dataset.field = field;
    inp.value = value == null ? "" : String(value);
    if (opts.placeholder) inp.placeholder = opts.placeholder;
    if (opts.textarea) inp.rows = 8;
    wrap.appendChild(l);
    wrap.appendChild(inp);
    dom.actionModalForm.appendChild(wrap);
  }

  addInput("to_name", "Nombre destinatario", p.to_name || "", { placeholder: "p. ej. SODIMAC" });
  addInput("to_email", "Correo destinatario", p.to_email || "", { type: "email", placeholder: "ventas@cliente.cl" });
  addInput("record_id", "ID registro vinculado (opcional)", p.record_id || "", { type: "number" });
  addInput("subject", "Asunto", p.subject || "Mensaje desde Odoo");
  addInput("body", "Mensaje", p.body || "", { textarea: true, placeholder: "Escribe el contenido del correo." });
}

function buildWorkflowFields(draft) {
  dom.actionModalForm.innerHTML = "";
  const p = (draft && draft.params) || {};
  function addInput(field, label, value, opts = {}) {
    const wrap = document.createElement("div");
    wrap.className = "modal-field";
    const l = document.createElement("span");
    l.className = "label";
    l.textContent = label;
    const inp = document.createElement("input");
    inp.type = opts.type || "text";
    inp.dataset.field = field;
    inp.value = value == null ? "" : String(value);
    if (opts.step) inp.step = opts.step;
    if (opts.min) inp.min = opts.min;
    if (opts.placeholder) inp.placeholder = opts.placeholder;
    wrap.appendChild(l);
    wrap.appendChild(inp);
    dom.actionModalForm.appendChild(wrap);
  }
  addInput("partner_name", "Cliente (se crea si no existe)", p.partner_name || "", { placeholder: "ACME SA" });
  addInput("product_name", "Producto/servicio (opcional)", p.product_name || "", { placeholder: "Consultoría" });
  addInput("amount", "Monto total", p.amount || 0, { type: "number", step: "0.01", min: "0" });
  addInput("qty", "Cantidad", p.qty || 1, { type: "number", step: "1", min: "1" });
  const note = document.createElement("p");
  note.className = "hint";
  note.style.margin = "0.4rem 0 0";
  note.textContent = "Pasos: buscar/crear cliente → cotización → confirmar venta → generar factura → validar factura.";
  dom.actionModalForm.appendChild(note);
}

function buildErpModal(draft) {
  const k = draft.kind;
  const spec = draft.spec || {};
  const wrap = document.createElement("div");
  wrap.className = "modal-plan-summary";
  if (k === "read") {
    dom.actionModalLead.textContent = `Consulta en Odoo · modelo ${spec.model || ""}`;
    dom.actionModalConfirm.textContent = "Ejecutar consulta";
    dom.actionModalCancel.textContent = "Cerrar";
  } else if (k === "write") {
    dom.actionModalLead.textContent = `Actualizar ${spec.model || ""} · id ${spec.record_id ?? "—"}`;
    dom.actionModalConfirm.textContent = "Guardar en Odoo";
    const hint = document.createElement("p");
    hint.className = "modal-erp-hint";
    hint.textContent = "Editá solo los campos necesarios; el servidor valida permisos y campos permitidos.";
    wrap.appendChild(hint);
    const fieldsBox = document.createElement("div");
    fieldsBox.className = "erp-write-fields";
    for (const [key, val] of Object.entries(spec.values || {})) {
      const row = document.createElement("div");
      row.className = "modal-field-row";
      const lab = document.createElement("label");
      lab.textContent = FIELD_LABELS[key] || key;
      let inp;
      if (typeof val === "boolean") {
        inp = document.createElement("input");
        inp.type = "checkbox";
        inp.dataset.field = key;
        inp.checked = val;
      } else if (typeof val === "number") {
        inp = document.createElement("input");
        inp.type = "number";
        inp.step = "any";
        inp.dataset.field = key;
        inp.value = String(val);
      } else {
        inp = document.createElement("input");
        inp.type = "text";
        inp.dataset.field = key;
        inp.value = val == null ? "" : String(val);
      }
      row.appendChild(lab);
      row.appendChild(inp);
      fieldsBox.appendChild(row);
    }
    wrap.appendChild(fieldsBox);
  } else if (k === "archive") {
    dom.actionModalLead.textContent = `Archivar (desactivar) registros en ${spec.model || ""}`;
    dom.actionModalConfirm.textContent = "Archivar en Odoo";
    const warn = document.createElement("p");
    warn.className = "modal-erp-warn";
    warn.textContent = `Se desactivarán los ids: ${(spec.record_ids || []).join(", ")}.`;
    wrap.appendChild(warn);
  } else if (k === "unlink") {
    dom.actionModalLead.textContent = "Eliminación física en Odoo";
    dom.actionModalConfirm.textContent = "Eliminar definitivamente";
    const warn = document.createElement("p");
    warn.className = "modal-erp-warn";
    warn.textContent = `Productos ids: ${(spec.record_ids || []).join(", ")}. Esta acción no se puede deshacer desde aquí.`;
    wrap.appendChild(warn);
  }
  const pre = document.createElement("pre");
  pre.className = "modal-erp-json";
  pre.textContent = JSON.stringify(spec, null, 2);
  wrap.appendChild(pre);
  dom.actionModalForm.appendChild(wrap);
}

function renderErpReadResult(data) {
  dom.actionModalTitle.textContent = data.title || "Consulta ERP";
  const meta = data.meta || {};
  dom.actionModalLead.textContent = `Modelo ${meta.model || ""} · ${Number(data.count || 0)} filas · límite ${meta.limit ?? "—"}`;
  dom.actionModalForm.innerHTML = "";
  const items = Array.isArray(data.items) ? data.items : [];
  const fields = Array.isArray(data.fields) ? data.fields : [];
  if (!items.length) {
    const box = document.createElement("div");
    box.className = "modal-plan-summary";
    const msg = document.createElement("div");
    msg.textContent = data.hint || "La consulta no devolvió filas.";
    box.appendChild(msg);
    dom.actionModalForm.appendChild(box);
    return;
  }
  const table = document.createElement("table");
  table.className = "modal-table";
  const thead = document.createElement("thead");
  const trh = document.createElement("tr");
  for (const f of fields) {
    const th = document.createElement("th");
    th.textContent = f;
    trh.appendChild(th);
  }
  thead.appendChild(trh);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  for (const it of items) {
    const tr = document.createElement("tr");
    for (const f of fields) {
      const td = document.createElement("td");
      const v = it[f];
      td.textContent = v == null ? "" : String(v);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  dom.actionModalForm.appendChild(table);
}

function renderDashboardResult(data) {
  dom.actionModalTitle.textContent = data.title || "Dashboard";
  dom.actionModalLead.textContent = "KPIs en tiempo real desde Odoo.";
  dom.actionModalForm.innerHTML = "";
  const k = data.kpis || {};
  const fmt = (n) => Number(n || 0).toLocaleString("es-CL");
  const grid = document.createElement("div");
  grid.className = "dashboard-grid";
  const cards = [
    { label: "Ventas (mes)", value: fmt(k.sales_month), kind: "accent", sub: "Confirmadas+terminadas" },
    { label: "Facturado (mes)", value: fmt(k.invoiced_month), kind: "success", sub: "Facturas cliente posteadas" },
    { label: "Vencido", value: fmt(k.overdue_amount), kind: "danger", sub: `${fmt(k.overdue_count)} factura(s)` },
    { label: "Compras (mes)", value: fmt(k.purchases_month), kind: "warn", sub: "Órdenes confirmadas" },
    { label: "Cotizaciones", value: fmt(k.open_quotations), kind: "", sub: "Borrador / enviadas" },
    { label: "Órdenes confirmadas", value: fmt(k.confirmed_orders), kind: "accent", sub: "Órdenes de venta activas" },
    { label: "Entregas pendientes", value: fmt(k.pickings_pending), kind: "warn", sub: "Pickings asignados/pendientes" },
    { label: "Borradores factura", value: fmt(k.draft_invoices), kind: "", sub: "Por revisar" },
  ];
  cards.forEach((c) => {
    const card = document.createElement("div");
    card.className = `kpi-card ${c.kind}`;
    const lab = document.createElement("div");
    lab.className = "kpi-label";
    lab.textContent = c.label;
    const val = document.createElement("div");
    val.className = "kpi-value";
    val.textContent = c.value;
    const sub = document.createElement("div");
    sub.className = "kpi-sub";
    sub.textContent = c.sub;
    card.appendChild(lab);
    card.appendChild(val);
    card.appendChild(sub);
    grid.appendChild(card);
  });
  dom.actionModalForm.appendChild(grid);
}

function renderListResult(data) {
  if (data.query === "dashboard_overview") return renderDashboardResult(data);
  if (data.query === "erp_read") return renderErpReadResult(data);

  dom.actionModalTitle.textContent = data.title || "Lista";
  dom.actionModalLead.textContent = `Total: ${Number(data.count || 0)} registros.`;
  dom.actionModalForm.innerHTML = "";
  const items = Array.isArray(data.items) ? data.items : [];
  const box = document.createElement("div");
  box.className = "modal-plan-summary";
  if (!items.length) {
    const msg = document.createElement("div");
    msg.textContent = data.hint || "Sin resultados.";
    box.appendChild(msg);
    dom.actionModalForm.appendChild(box);
    return;
  }
  const HEADER_LABELS = {
    // Comunes
    id: "ID",
    name: "Nombre",
    title: "Título",
    count: "Cantidad",
    customer: "Cliente",
    vendor: "Proveedor",
    partner: "Contacto",
    document: "Documento",
    date_order: "Fecha",
    invoice_date: "Fecha factura",
    invoice_date_due: "Vencimiento",
    write_date: "Última actualización",
    amount_total: "Total",
    residual: "Saldo",
    payment_state: "Estado de pago",
    state: "Estado",
    move_type: "Tipo",
    currency: "Moneda",
    roles: "Roles / grupos",
    active: "Activo",
    internal_user: "Tipo usuario",
    delivery_status: "Entrega",
    invoice_status: "Factura",
    // List queries específicas
    order_ref: "Orden",
    duplicate_flag: "Control duplicado",
    issues: "Problemas",
    entity: "Entidad",
    record: "Registro",
    missing_fields: "Campos faltantes",
    product: "Producto",
    min_qty: "Mínimo",
    max_qty: "Máximo",
    suggested_qty: "Sugerido",
    suggested_action: "Acción",
    sold_qty_90d: "Vendido 90 d",
    avg_monthly: "Prom. mensual",
    trend_pct: "Tendencia %",
    forecast_horizon_qty: "Pronóstico",
    purchase_hint: "Sugerencia compras",
    best_option: "Mejor opción",
    lead_days: "Lead time (días)",
    drop_pct: "Caída %",
    month_prev2_sales: "Mes -2",
    month_prev1_sales: "Mes -1",
    has_active_contract: "Contrato activo",
    billing_incidents: "Incidencias facturación",
  };

  const VALUE_MAPS = {
    state: {
      draft: "Borrador",
      sent: "Enviada",
      sale: "Confirmada",
      done: "Hecha/Cerrada",
      posted: "Publicado",
      cancel: "Cancelado",
      cancelled: "Cancelado",
    },
    invoice_status: {
      "to invoice": "Por facturar",
      upselling: "Venta adicional",
      invoiced: "Facturada",
      no: "Nada que facturar",
    },
    payment_state: {
      not_paid: "No pagado",
      paid: "Pagado",
      partial: "Pago parcial",
      in_payment: "En pago",
      reversed: "Revertido",
    },
    delivery_status: {
      pending: "Pendiente",
      partially: "Parcial",
      full: "Completa",
    },
    move_type: {
      out_invoice: "Factura cliente",
      in_invoice: "Factura proveedor",
      entry: "Asiento contable",
      out_refund: "Nota de crédito cliente",
      in_refund: "Nota de crédito proveedor",
    },
    internal_user: {
      true: "Portal/Compartido",
      false: "Interno",
    },
    active: {
      true: "Sí",
      false: "No",
    },
    has_active_contract: {
      true: "Sí",
      false: "No",
    },
  };

  function formatCellValue(key, value) {
    if (value == null) return "";
    const map = VALUE_MAPS[key];
    if (map) {
      const raw = typeof value === "string" ? value.trim() : value;
      const norm =
        typeof raw === "string" ? raw.toLowerCase() : raw === true ? "true" : raw === false ? "false" : String(raw);
      if (Object.prototype.hasOwnProperty.call(map, norm)) return map[norm];
      if (typeof raw === "string" && Object.prototype.hasOwnProperty.call(map, raw)) return map[raw];
    }
    return String(value);
  }

  const keys = Object.keys(items[0] || {});
  const table = document.createElement("table");
  table.className = "modal-table";
  const thead = document.createElement("thead");
  thead.innerHTML =
    "<tr>" +
    keys
      .map((k) => `<th>${escapeModalHtml(HEADER_LABELS[k] || k)}</th>`)
      .join("") +
    "</tr>";
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  items.forEach((it) => {
    const tr = document.createElement("tr");
    tr.innerHTML = keys
      .map((k) => `<td>${escapeModalHtml(formatCellValue(k, it[k]))}</td>`)
      .join("");
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  attachListRowLinks({ query: data.query, items, tbody });
  box.appendChild(table);
  dom.actionModalForm.appendChild(box);
}

export function openActionModal(draft) {
  state.pendingActionDraft = draft;
  dom.actionModalTitle.textContent = draft.summary || "Confirmar inserción";
  dom.actionModalForm.innerHTML = "";
  dom.actionModalForm.appendChild(buildImpactCard(draft));
  dom.actionModalConfirm.hidden = false;
  dom.actionModalCancel.textContent = "Cancelar";
  dom.actionModalConfirm.textContent = "Insertar en Odoo";

  if (draft.operation === "product_setup" && draft.plan) {
    dom.actionModalLead.textContent = "Revisá el resumen y confirmá para crear el producto y las reglas en Odoo.";
    // En esta versión modular, mostramos solo impacto; el server ejecuta plan.
  } else if (draft.operation === "list" && draft.query) {
    dom.actionModalLead.textContent = "Consulta de registros en Odoo.";
    dom.actionModalCancel.textContent = "Cerrar";
    dom.actionModalConfirm.textContent = "Actualizar lista";
    const info = document.createElement("div");
    info.className = "modal-plan-summary";
    info.textContent = "Cargando lista…";
    dom.actionModalForm.appendChild(info);
  } else if (draft.operation === "email") {
    dom.actionModalLead.textContent = "Enviar correo desde Odoo (mail.mail).";
    dom.actionModalConfirm.textContent = "Enviar correo";
    buildEmailFields(draft);
  } else if (draft.operation === "workflow") {
    dom.actionModalLead.textContent = "Workflow encadenado: cliente → cotización → venta → factura → validación.";
    dom.actionModalConfirm.textContent = "Ejecutar flujo";
    buildWorkflowFields(draft);
  } else if (draft.operation === "erp" && draft.kind) {
    dom.actionModalTitle.textContent = draft.summary || `ERP · ${draft.kind}`;
    buildErpModal(draft);
  } else if (draft.values && draft.model) {
    dom.actionModalLead.textContent = `${draft.model} · alta nueva`;
    buildModalFields(draft.model, draft.values);
  }

  dom.actionModal.hidden = false;
  document.body.style.overflow = "hidden";
  dom.actionModalConfirm.disabled = false;
}

export async function confirmActionInsert() {
  const draft = state.pendingActionDraft;
  if (!draft) return;
  dom.actionModalConfirm.disabled = true;
  try {
    if (draft.operation === "erp" && draft.kind) {
      const kind = draft.kind;
      let spec = { ...(draft.spec || {}) };
      if (kind === "write") {
        const g = gatherModalValues();
        spec = { ...spec, values: { ...(spec.values || {}), ...g } };
      }
      const { ok, data } = await apiFetchJson("/api/action/erp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, spec }),
      });
      if (!ok) {
        showToast(formatDetail(data.detail) || "Error en operación ERP.", true);
        appendMessage("assistant", formatDetail(data.detail) || "Odoo rechazó la operación.");
        dom.actionModalConfirm.disabled = false;
        return;
      }
      if (kind === "read") {
        renderErpReadResult(data);
        dom.actionModalConfirm.disabled = false;
        return;
      }
      showToast("Listo.");
      appendMessage("assistant", "Operación aplicada en Odoo.", { odoo_links: data.odoo_links || [] });
      closeActionModal();
      return;
    }

    if (draft.operation === "list" && draft.query) {
      const { ok, data } = await apiFetchJson("/api/action/list", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operation: "list", query: draft.query, params: draft.params || {} }),
      });
      if (!ok) {
        showToast(formatDetail(data.detail) || "No se pudo cargar la lista.", true);
        appendMessage("assistant", formatDetail(data.detail) || "No se pudo obtener la lista solicitada.");
        dom.actionModalConfirm.disabled = false;
        return;
      }
      renderListResult(data);
      dom.actionModalConfirm.disabled = false;
      return;
    }

    if (draft.operation === "email") {
      const vals = gatherModalValues();
      const target = vals._target || draft.target || "partner";
      const params = {
        to_name: vals.to_name || "",
        to_email: vals.to_email || "",
        subject: vals.subject || "",
        body: vals.body || "",
        record_id: vals.record_id ? Number(vals.record_id) : 0,
      };
      const { ok, data } = await apiFetchJson("/api/action/email", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operation: "email", target, params }),
      });
      if (!ok) {
        showToast(formatDetail(data.detail) || "No se pudo enviar el correo.", true);
        appendMessage("assistant", formatDetail(data.detail) || "No se pudo enviar el correo.");
        dom.actionModalConfirm.disabled = false;
        return;
      }
      showToast(`Correo enviado a ${data.to}`);
      appendMessage("assistant", `Correo enviado por Odoo (mail.mail id ${data.mail_id}) a ${data.to}.`);
      closeActionModal();
      return;
    }

    if (draft.operation === "workflow") {
      const vals = gatherModalValues();
      const draftParams = draft.params || {};
      const params = {
        partner_name: vals.partner_name || draftParams.partner_name || draftParams.customer_name || "",
        product_name: vals.product_name || draftParams.product_name || "",
        amount: vals.amount && Number(vals.amount) > 0 ? Number(vals.amount) : Number(draftParams.amount || 0),
        qty: vals.qty && Number(vals.qty) > 0 ? Number(vals.qty) : Number(draftParams.qty || 1),
      };
      const { ok, data } = await apiFetchJson("/api/action/workflow", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operation: "workflow", name: draft.name, params }),
      });
      if (!ok) {
        showToast(formatDetail(data.detail) || "Falló el workflow.", true);
        appendMessage("assistant", formatDetail(data.detail) || "Falló el workflow en Odoo.");
        dom.actionModalConfirm.disabled = false;
        return;
      }
      const steps = Array.isArray(data.steps) ? data.steps : [];
      const lines = [];
      lines.push(data.ok ? "Workflow ejecutado en Odoo correctamente." : "Workflow ejecutado con incidencias. Revisa los pasos:");
      for (const s of steps) lines.push(`- ${s.ok ? "OK" : "FALLÓ"} · ${s.step || "Paso"}: ${s.detail || ""}`);
      appendMessage("assistant", lines.join("\n"));
      closeActionModal();
      return;
    }

    if (draft.operation === "product_setup") {
      const { ok, data } = await apiFetchJson("/api/action/product-setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan: draft.plan }),
      });
      if (!ok) {
        showToast(formatDetail(data.detail) || "Falló la configuración en Odoo.", true);
        appendMessage("assistant", formatDetail(data.detail) || "No se pudo ejecutar el plan.");
        dom.actionModalConfirm.disabled = false;
        return;
      }
      appendMessage("assistant", `Configuración aplicada en Odoo.\n${(data.log || []).join("\n")}`, { odoo_links: data.odoo_links });
      showToast("Producto y reglas aplicadas en Odoo.");
      closeActionModal();
      await refreshHealth();
      return;
    }

    const values = gatherModalValues();
    const { ok, data } = await apiFetchJson("/api/action/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: draft.model, operation: "create", values }),
    });
    if (!ok) {
      showToast(formatDetail(data.detail) || "Odoo rechazó la creación.", true);
      appendMessage("assistant", formatDetail(data.detail) || "No se pudo crear el registro en Odoo.");
      dom.actionModalConfirm.disabled = false;
      return;
    }
    showToast(`Creado en Odoo (${draft.model} id=${data.id}).`);
    appendMessage("assistant", `Listo: se creó el registro en Odoo (${draft.model}, id ${data.id}).`, { odoo_links: data.odoo_links });
    closeActionModal();
    await refreshHealth();
  } catch (err) {
    showToast(String(err), true);
    dom.actionModalConfirm.disabled = false;
  }
}

export function attachActionModalHandlers() {
  if (!dom.actionModal) return;
  dom.actionModalCancel.addEventListener("click", () => closeActionModal());
  dom.actionModalConfirm.addEventListener("click", () => confirmActionInsert());
  dom.actionModal.addEventListener("click", (e) => {
    if (e.target === dom.actionModal) closeActionModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !dom.actionModal.hidden) {
      e.preventDefault();
      closeActionModal();
    }
  });
}

