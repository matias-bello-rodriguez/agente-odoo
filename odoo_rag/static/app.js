const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("input");
const composer = document.getElementById("composer");
const btnSend = document.getElementById("btnSend");
const btnClear = document.getElementById("btnClear");
const btnRebuild = document.getElementById("btnRebuild");
const topKEl = document.getElementById("topK");
const healthPill = document.getElementById("healthPill");
const odooTarget = document.getElementById("odooTarget");
const indexState = document.getElementById("indexState");
const toast = document.getElementById("toast");
const btnMic = document.getElementById("btnMic");
const micLabel = document.getElementById("micLabel");
const micHint = document.getElementById("micHint");
const actionModal = document.getElementById("actionModal");
const actionModalTitle = document.getElementById("actionModalTitle");
const actionModalLead = document.getElementById("actionModalLead");
const actionModalForm = document.getElementById("actionModalForm");
const actionModalCancel = document.getElementById("actionModalCancel");
const actionModalConfirm = document.getElementById("actionModalConfirm");
let odooBaseUrl = "";

/** Borrador vigente para el modal (solo referencia del modelo permitido por el servidor). */
let pendingActionDraft = null;

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

/** @type {SpeechRecognition | null} */
let recognition = null;
let listening = false;
/** Texto ya fijado por reconocimiento (solo bloques finales). */
let speechFinalTail = "";
/** Prefijo original del campo al iniciar dictado (no se sobrescribe). */
let speechPrefix = "";

/** ms sin nuevos eventos de voz antes de enviar automáticamente */
const VOICE_SILENCE_MS = 2000;

/** @type {ReturnType<typeof setTimeout> | null} */
let voiceSilenceTimer = null;

const COMPOSER_MIN_H = 48;
const COMPOSER_MAX_H_CAP = 280;

function autoResizeComposer() {
  const el = inputEl;
  if (!el) return;
  el.style.height = `${COMPOSER_MIN_H}px`;
  const maxH = Math.min(
    Math.round(window.innerHeight * 0.42),
    COMPOSER_MAX_H_CAP
  );
  const next = Math.min(Math.max(el.scrollHeight, COMPOSER_MIN_H), maxH);
  el.style.height = `${next}px`;
}

function resetComposerHeight() {
  if (!inputEl) return;
  inputEl.style.height = "";
  autoResizeComposer();
}

function formatDetail(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail.map((x) => (x.msg ? x.msg : JSON.stringify(x))).join(" ");
  try {
    return JSON.stringify(detail);
  } catch {
    return "";
  }
}

function tryOfferSuggestedAction(detail) {
  if (!detail || typeof detail !== "object") return false;
  if (!["PARTNER_NOT_FOUND", "VENDOR_NOT_FOUND"].includes(detail.code)) return false;
  const msg =
    detail.message ||
    "No encontré el contacto requerido. ¿Quieres crearlo ahora?";
  appendMessage("assistant", msg);
  const suggestion = detail.suggested_action;
  if (!suggestion || suggestion.operation !== "create" || !suggestion.values) {
    return true;
  }
  const yes = window.confirm(
    `${msg}\n\nSi eliges Aceptar, abriremos el formulario para crear el cliente.`
  );
  if (yes) {
    closeActionModal();
    openActionModal(suggestion);
  }
  return true;
}

function showToast(text, isError = false) {
  toast.textContent = text;
  toast.hidden = false;
  toast.classList.toggle("error", isError);
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => {
    toast.hidden = true;
  }, 4200);
}

function appendMessage(role, text, options = {}) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}${options.loading ? " loading" : ""}`;
  const meta = document.createElement("div");
  meta.className = "msg-meta";
  meta.textContent = role === "user" ? "Tú" : "Asistente";
  const body = document.createElement("div");
  body.className = "msg-body";
  body.textContent = text;
  wrap.appendChild(meta);
  wrap.appendChild(body);
  const links = options.odoo_links;
  if (
    role === "assistant" &&
    Array.isArray(links) &&
    links.length > 0
  ) {
    const bar = document.createElement("div");
    bar.className = "msg-odoo-links";
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", "Enlaces a Odoo");
    for (const item of links) {
      if (!item || !item.url) continue;
      const a = document.createElement("a");
      a.className = "msg-odoo-link";
      a.href = item.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = item.label || "Abrir en Odoo";
      bar.appendChild(a);
    }
    if (bar.childElementCount > 0) wrap.appendChild(bar);
  }
  messagesEl.appendChild(wrap);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return wrap;
}

async function refreshHealth() {
  try {
    const r = await fetch("/api/health");
    const data = await r.json();
    healthPill.textContent = data.ok ? "En línea" : "Error";
    healthPill.classList.toggle("warn", !data.ok);
    odooBaseUrl = String(data.odoo_url || "").replace(/\/+$/, "");
    odooTarget.textContent = `${data.odoo_url} · ${data.odoo_db}`;
    indexState.textContent = data.indexed ? "Listo" : "Sin índice";
    indexState.classList.toggle("dead", !data.indexed);
    if (!data.openai_configured) {
      showToast("Falta OPENAI_API_KEY en .env", true);
    }
  } catch {
    healthPill.textContent = "Sin conexión";
    healthPill.classList.add("warn");
  }
}

function odooRecordUrl(model, recordId) {
  const id = Number(recordId || 0);
  if (!model || !id) return "";
  const base = odooBaseUrl || "";
  if (!base) return "";
  return `${base}/web#id=${id}&model=${encodeURIComponent(model)}&view_type=form`;
}

function modelForListQuery(query) {
  if (query === "users_roles" || query === "users_last_login") return "res.users";
  if (query === "accounting_recent_actions" || query === "accounting_missing_key_data") {
    return "account.move";
  }
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

function clearVoiceSilenceTimer() {
  if (voiceSilenceTimer !== null) {
    clearTimeout(voiceSilenceTimer);
    voiceSilenceTimer = null;
  }
}

/** Tras un silencio tras la última palabra reconocida, envía el formulario. */
function scheduleVoiceAutoSubmit() {
  clearVoiceSilenceTimer();
  voiceSilenceTimer = setTimeout(() => {
    voiceSilenceTimer = null;
    if (!listening) return;
    const text = inputEl.value.trim();
    if (!text) return;
    stopDictation(true);
    composer.requestSubmit();
  }, VOICE_SILENCE_MS);
}

composer.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearVoiceSilenceTimer();
  if (listening) stopDictation(true);
  const text = inputEl.value.trim();
  if (!text) return;

  appendMessage("user", text);
  inputEl.value = "";
  resetComposerHeight();
  const loading = appendMessage("assistant", "Pensando…", { loading: true });
  btnSend.disabled = true;

  try {
    const top_k = Number(topKEl.value) || 6;
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, top_k }),
    });
    const data = await res.json().catch(() => ({}));
    loading.remove();
    if (!res.ok) {
      appendMessage("assistant", formatDetail(data.detail) || res.statusText || "Error al consultar.");
      showToast(formatDetail(data.detail) || "Error en la consulta", true);
      return;
    }
    appendMessage("assistant", data.reply || "");
    if (
      data.draft_action &&
      data.draft_action.operation === "product_setup" &&
      data.draft_action.plan
    ) {
      openActionModal(data.draft_action);
    } else if (data.draft_action && data.draft_action.operation === "list") {
      openActionModal(data.draft_action);
      await confirmActionInsert();
    } else if (data.draft_action && data.draft_action.operation === "email") {
      openActionModal(data.draft_action);
    } else if (data.draft_action && data.draft_action.operation === "workflow") {
      openActionModal(data.draft_action);
    } else if (data.draft_action && data.draft_action.operation === "erp") {
      openActionModal(data.draft_action);
      if (data.draft_action.kind === "read") {
        await confirmActionInsert();
      }
    } else if (data.draft_action && data.draft_action.values) {
      openActionModal(data.draft_action);
    }
  } catch (err) {
    loading.remove();
    appendMessage("assistant", String(err));
    showToast("No se pudo contactar al servidor.", true);
  } finally {
    btnSend.disabled = false;
    inputEl.focus();
    autoResizeComposer();
  }
});

btnClear.addEventListener("click", () => {
  messagesEl.innerHTML = "";
});

btnRebuild.addEventListener("click", async () => {
  const ok = window.confirm(
    "¿Reindexar desde Odoo? Puede tardar varios minutos y consumir cuota de OpenAI."
  );
  if (!ok) return;
  btnRebuild.disabled = true;
  showToast("Reindexando… no cierres esta pestaña.");
  try {
    const res = await fetch("/api/index/rebuild", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showToast(formatDetail(data.detail) || "Falló la reindexación", true);
      return;
    }
    showToast("Índice actualizado.");
    await refreshHealth();
  } catch (err) {
    showToast(String(err), true);
  } finally {
    btnRebuild.disabled = false;
  }
});

inputEl.addEventListener("input", autoResizeComposer);
inputEl.addEventListener("paste", () => requestAnimationFrame(autoResizeComposer));

window.addEventListener("resize", () => {
  autoResizeComposer();
});

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    composer.requestSubmit();
  }
});

function speechSupported() {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

function speechLang() {
  const nav = navigator.language || "";
  return nav.toLowerCase().startsWith("es") ? nav : "es-ES";
}

function setListeningUI(active) {
  listening = active;
  btnMic.classList.toggle("listening", active);
  btnMic.setAttribute("aria-pressed", active ? "true" : "false");
  micLabel.textContent = active ? "Parar" : "Dictar";
  micHint.hidden = !active;
  micHint.textContent = active
    ? "Escuchando… al callar un momento se envía la pregunta."
    : "";
}

function attachSpeechHandlers(rec) {
  rec.onresult = (event) => {
    let interim = "";
    let finals = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const phrase = event.results[i][0].transcript;
      if (event.results[i].isFinal) finals += phrase;
      else interim += phrase;
    }
    speechFinalTail += finals;
    inputEl.value = speechPrefix + speechFinalTail + interim;
    inputEl.scrollTop = inputEl.scrollHeight;
    autoResizeComposer();
    scheduleVoiceAutoSubmit();
  };

  rec.onerror = (event) => {
    if (event.error === "aborted" || event.error === "no-speech") return;
    showToast(
      event.error === "not-allowed"
        ? "Permiso de micrófono denegado. Revísalo en la barra del navegador."
        : `Micrófono: ${event.error}`,
      true
    );
    stopDictation(true);
  };

  rec.onend = () => {
    if (!listening) return;
    try {
      rec.start();
    } catch {
      /* algunos navegadores ya reinician solos */
    }
  };
}

function startDictation() {
  if (!speechSupported()) {
    showToast(
      "Tu navegador no expone reconocimiento de voz (prueba Chrome o Edge en http://localhost).",
      true
    );
    return;
  }

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SR();
  recognition.lang = speechLang();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;

  speechPrefix = inputEl.value;
  speechFinalTail = "";
  attachSpeechHandlers(recognition);

  try {
    recognition.start();
    setListeningUI(true);
    showToast("Micrófono activo: al dejar de hablar se envía solo.");
  } catch (err) {
    recognition = null;
    showToast(`No se pudo iniciar el micrófono: ${err}`, true);
  }
}

function stopDictation(silent) {
  clearVoiceSilenceTimer();
  listening = false;
  const rec = recognition;
  recognition = null;
  if (rec) {
    try {
      rec.stop();
    } catch {
      /* ignorar */
    }
  }
  setListeningUI(false);
  speechFinalTail = "";
  speechPrefix = "";
  if (!silent) inputEl.focus();
}

btnMic.addEventListener("click", () => {
  if (!speechSupported()) {
    showToast(
      "Sin API de reconocimiento de voz: usa Chrome/Edge sobre http://127.0.0.1 u https.",
      true
    );
    return;
  }
  if (listening) stopDictation(false);
  else startDictation();
});

if (!speechSupported()) {
  btnMic.disabled = true;
  btnMic.title = "El reconocimiento de voz solo está en Chrome/Edge y contexto seguro.";
}

function closeActionModal() {
  pendingActionDraft = null;
  actionModal.hidden = true;
  actionModalForm.innerHTML = "";
  document.body.style.overflow = "";
}

function labelForField(key) {
  return FIELD_LABELS[key] || key;
}

function buildModalFields(model, values) {
  actionModalForm.innerHTML = "";
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
      actionModalForm.appendChild(wrap);
      continue;
    }

    const lbl = document.createElement("span");
    lbl.className = "label";
    lbl.textContent = labelForField(key);
    wrap.appendChild(lbl);

    if (key === "comment") {
      const ta = document.createElement("textarea");
      ta.dataset.field = key;
      ta.value = val == null ? "" : String(val);
      wrap.appendChild(ta);
      actionModalForm.appendChild(wrap);
      continue;
    }
    if (key === "narration" || key === "note" || key === "notes") {
      const ta = document.createElement("textarea");
      ta.dataset.field = key;
      ta.value = val == null ? "" : String(val);
      wrap.appendChild(ta);
      actionModalForm.appendChild(wrap);
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
      actionModalForm.appendChild(wrap);
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
      actionModalForm.appendChild(wrap);
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
      actionModalForm.appendChild(wrap);
      continue;
    }

    const inp = document.createElement("input");
    inp.dataset.field = key;
    if (
      key === "list_price" ||
      key === "standard_price" ||
      key === "invoice_line_price_unit" ||
      key === "invoice_line_qty" ||
      key === "order_line_qty" ||
      key === "order_line_price_unit" ||
      key === "order_line_discount" ||
      key === "move_line_qty"
    ) {
      inp.type = "number";
      inp.step = "0.01";
      inp.min = "0";
      inp.value = val == null || val === "" ? "" : Number(val);
    } else {
      inp.type = "text";
      inp.value = val == null ? "" : String(val);
    }
    wrap.appendChild(inp);
    actionModalForm.appendChild(wrap);
  }
}

function buildEmailFields(draft) {
  actionModalForm.innerHTML = "";
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
  actionModalForm.appendChild(targetWrap);

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
    actionModalForm.appendChild(wrap);
  }
  addInput("to_name", "Nombre destinatario", p.to_name || "", { placeholder: "p. ej. SODIMAC" });
  addInput("to_email", "Correo destinatario", p.to_email || "", { type: "email", placeholder: "ventas@cliente.cl" });
  addInput("record_id", "ID registro vinculado (opcional)", p.record_id || "", { type: "number" });
  addInput("subject", "Asunto", p.subject || "Mensaje desde Odoo");
  addInput("body", "Mensaje", p.body || "", { textarea: true, placeholder: "Escribe el contenido del correo. Soporta saltos de línea." });
}

function buildWorkflowFields(draft) {
  actionModalForm.innerHTML = "";
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
    actionModalForm.appendChild(wrap);
  }
  addInput("partner_name", "Cliente (se crea si no existe)", p.partner_name || "", { placeholder: "ACME SA" });
  addInput("product_name", "Producto/servicio (opcional)", p.product_name || "", { placeholder: "Consultoría" });
  addInput("amount", "Monto total", p.amount || 0, { type: "number", step: "0.01", min: "0" });
  addInput("qty", "Cantidad", p.qty || 1, { type: "number", step: "1", min: "1" });

  const note = document.createElement("p");
  note.className = "hint";
  note.style.margin = "0.4rem 0 0";
  note.textContent = "Pasos: buscar/crear cliente → cotización → confirmar venta → generar factura → validar factura.";
  actionModalForm.appendChild(note);
}

/** Resumen legible del plan product_setup (sin JSON crudo). */
function buildProductSetupSummary(plan) {
  const root = document.createElement("div");
  root.className = "modal-plan-summary";

  function addRow(label, text) {
    const row = document.createElement("div");
    row.className = "modal-summary-row";
    const k = document.createElement("strong");
    k.textContent = label;
    const v = document.createElement("span");
    v.textContent = text == null || text === "" ? "—" : String(text);
    row.appendChild(k);
    row.appendChild(v);
    root.appendChild(row);
  }

  const p = plan || {};
  addRow("Producto", p.product_name);
  addRow("Referencia interna", p.internal_reference);
  addRow("Categoría", p.category_name);

  const cur = p.currency_code ? ` ${String(p.currency_code)}` : "";
  addRow(
    "Precios",
    `${p.list_price ?? "—"} venta · ${p.standard_price ?? "—"} costo${cur}`.trim()
  );

  addRow(
    "Ventas / compras",
    `${p.sale_ok !== false ? "Se vende" : "No venta"} · ${p.purchase_ok !== false ? "Se compra" : "No compra"}`
  );

  addRow("Trazabilidad", p.tracking || "—");
  if (p.weight_kg != null && p.weight_kg !== "") {
    addRow("Peso", `${p.weight_kg} kg`);
  }

  addRow(
    "Valorización categoría",
    p.category_fifo_realtime !== false ? "FIFO / tiempo real (según categoría)" : "Según categoría"
  );

  const suppliers = Array.isArray(p.suppliers) ? p.suppliers : [];
  if (suppliers.length) {
    const wrap = document.createElement("div");
    wrap.className = "modal-suppliers-wrap";
    const lbl = document.createElement("strong");
    lbl.textContent = "Proveedores";
    const ul = document.createElement("ul");
    suppliers.forEach((s) => {
      const li = document.createElement("li");
      const name = s.name != null ? String(s.name) : "—";
      const price = s.price != null ? s.price : "—";
      const minQ = s.min_qty != null ? s.min_qty : "—";
      const lead = s.lead_days != null ? s.lead_days : "—";
      li.textContent = `${name}: precio ${price}, cant. mín. ${minQ}, entrega ${lead} días`;
      ul.appendChild(li);
    });
    wrap.appendChild(lbl);
    wrap.appendChild(ul);
    root.appendChild(wrap);
  }

  addRow(
    "Reorden",
    `mín. ${p.reorder_min ?? "—"} · máx. ${p.reorder_max ?? "—"} · reposición automática ${p.auto_replenishment !== false ? "sí" : "no"}`
  );

  const notes = p.note_accounts;
  if (notes != null && String(notes).trim()) {
    const noteEl = document.createElement("div");
    noteEl.className = "modal-notes";
    noteEl.textContent = String(notes).trim();
    root.appendChild(noteEl);
  }

  return root;
}

function buildErpModal(draft) {
  const k = draft.kind;
  const spec = draft.spec || {};
  const wrap = document.createElement("div");
  wrap.className = "modal-plan-summary";
  if (k === "read") {
    actionModalLead.textContent = `Consulta en Odoo · modelo ${spec.model || ""}`;
    actionModalConfirm.textContent = "Ejecutar consulta";
    actionModalCancel.textContent = "Cerrar";
  } else if (k === "write") {
    actionModalLead.textContent = `Actualizar ${spec.model || ""} · id ${spec.record_id ?? "—"}`;
    actionModalConfirm.textContent = "Guardar en Odoo";
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
    actionModalLead.textContent = `Archivar (desactivar) registros en ${spec.model || ""}`;
    actionModalConfirm.textContent = "Archivar en Odoo";
    const warn = document.createElement("p");
    warn.className = "modal-erp-warn";
    warn.textContent = `Se desactivarán los ids: ${(spec.record_ids || []).join(", ")}.`;
    wrap.appendChild(warn);
  } else if (k === "unlink") {
    actionModalLead.textContent = "Eliminación física en Odoo";
    actionModalConfirm.textContent = "Eliminar definitivamente";
    const warn = document.createElement("p");
    warn.className = "modal-erp-warn";
    warn.textContent = `Productos ids: ${(spec.record_ids || []).join(", ")}. Esta acción no se puede deshacer desde aquí.`;
    wrap.appendChild(warn);
  }
  const pre = document.createElement("pre");
  pre.className = "modal-erp-json";
  pre.textContent = JSON.stringify(spec, null, 2);
  wrap.appendChild(pre);
  actionModalForm.appendChild(wrap);
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
    out.bullets = [
      "No modifica registros existentes.",
      "Puedes archivarlo o editarlo luego según modelo.",
    ];
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
      out.bullets = [
        `Campos estimados a cambiar: ${nFields}.`,
        "Requiere validar datos antes de confirmar.",
      ];
      return out;
    }
    if (kind === "archive") {
      const ids = Array.isArray(spec.record_ids) ? spec.record_ids : [];
      out.summary = `Archivará (desactivará) registros en ${spec.model || "Odoo"}.`;
      out.records = `${ids.length} registro(s)`;
      out.risk = "Medio";
      out.reversible = "Sí (reactivando active=true)";
      out.bullets = [
        "No borra físicamente los registros.",
        "Puede afectar listados y reportes por estado activo.",
      ];
      return out;
    }
    if (kind === "unlink") {
      const ids = Array.isArray(spec.record_ids) ? spec.record_ids : [];
      out.summary = `Eliminará definitivamente registros en ${spec.model || "Odoo"}.`;
      out.records = `${ids.length} registro(s)`;
      out.risk = "Alto";
      out.reversible = "No";
      out.bullets = [
        "Borrado físico (hard delete).",
        "Puede romper trazabilidad y referencias históricas.",
      ];
      return out;
    }
    return out;
  }

  if (op === "list") {
    out.summary = "Consulta analítica/listado sin escritura en Odoo.";
    out.records = "Variable (según query)";
    out.risk = "Bajo";
    out.reversible = "Sí (solo lectura)";
    out.bullets = [
      "No modifica datos del ERP.",
      "Puede consumir tiempo si la consulta es grande.",
    ];
    return out;
  }

  if (op === "email") {
    out.summary = "Enviará un correo desde Odoo.";
    out.records = "1 mensaje";
    out.risk = "Medio";
    out.reversible = "No (si ya fue entregado)";
    out.bullets = [
      "Revisar destinatario y asunto antes de confirmar.",
      "Puede generar comunicación externa inmediata.",
    ];
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
    out.bullets = [
      "Impacta inventario, reposición y compras.",
      "Revisar parámetros de stock mínimo y lead times.",
    ];
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

function escapeModalHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => {
    const map = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return map[c] || c;
  });
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
        ? `<ul class="modal-impact-list">${bullets
            .map((it) => `<li>${escapeModalHtml(it)}</li>`)
            .join("")}</ul>`
        : ""
    }
  `;
  return card;
}

function openActionModal(draft) {
  pendingActionDraft = draft;
  actionModalTitle.textContent = draft.summary || "Confirmar inserción";
  actionModalForm.innerHTML = "";
  actionModalForm.appendChild(buildImpactCard(draft));
  actionModalConfirm.hidden = false;
  actionModalCancel.textContent = "Cancelar";
  actionModalConfirm.textContent = "Insertar en Odoo";
  if (draft.operation === "product_setup" && draft.plan) {
    actionModalLead.textContent =
      "Revisá el resumen y confirmá para crear el producto y las reglas en Odoo.";
    actionModalForm.appendChild(buildProductSetupSummary(draft.plan));
  } else if (draft.operation === "list" && draft.query) {
    actionModalLead.textContent = "Consulta de registros en Odoo.";
    actionModalCancel.textContent = "Cerrar";
    actionModalConfirm.textContent = "Actualizar lista";
    const info = document.createElement("div");
    info.className = "modal-plan-summary";
    info.textContent = "Cargando lista…";
    actionModalForm.appendChild(info);
  } else if (draft.operation === "email") {
    actionModalLead.textContent = "Enviar correo desde Odoo (mail.mail).";
    actionModalConfirm.textContent = "Enviar correo";
    buildEmailFields(draft);
  } else if (draft.operation === "workflow") {
    actionModalLead.textContent = "Workflow encadenado: cliente → cotización → venta → factura → validación.";
    actionModalConfirm.textContent = "Ejecutar flujo";
    buildWorkflowFields(draft);
  } else if (draft.operation === "erp" && draft.kind) {
    actionModalTitle.textContent = draft.summary || `ERP · ${draft.kind}`;
    buildErpModal(draft);
  } else {
    actionModalLead.textContent = `${draft.model} · alta nueva`;
    buildModalFields(draft.model, draft.values);
  }
  actionModal.hidden = false;
  document.body.style.overflow = "hidden";
  actionModalConfirm.disabled = false;
}

function gatherModalValues() {
  const out = {};
  actionModalForm.querySelectorAll("[data-field]").forEach((el) => {
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

async function confirmActionInsert() {
  if (!pendingActionDraft) return;
  actionModalConfirm.disabled = true;
  try {
    if (pendingActionDraft.operation === "erp" && pendingActionDraft.kind) {
      const kind = pendingActionDraft.kind;
      let spec = { ...(pendingActionDraft.spec || {}) };
      if (kind === "write") {
        const g = gatherModalValues();
        spec = { ...spec, values: { ...(spec.values || {}), ...g } };
      }
      const res = await fetch("/api/action/erp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, spec }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(formatDetail(data.detail) || "Error en operación ERP.", true);
        appendMessage(
          "assistant",
          formatDetail(data.detail) || "Odoo rechazó la operación."
        );
        actionModalConfirm.disabled = false;
        return;
      }
      if (kind === "read") {
        renderErpReadResult(data);
        actionModalConfirm.disabled = false;
        return;
      }
      let msg = "Operación aplicada en Odoo.";
      if (kind === "write")
        msg = `Actualizado ${data.model || ""} id ${data.id ?? ""}. Campos: ${(data.updated || []).join(", ")}.`;
      else if (kind === "archive")
        msg = `Archivados (desactivados) en ${data.model || ""}: ids ${(data.archived_ids || []).join(", ")}.`;
      else if (kind === "unlink")
        msg = `Eliminados en ${data.model || ""}: ids ${(data.deleted_ids || []).join(", ")}.`;
      showToast("Listo.");
      appendMessage("assistant", msg, { odoo_links: data.odoo_links || [] });
      closeActionModal();
      return;
    }
    if (pendingActionDraft.operation === "list" && pendingActionDraft.query) {
      const res = await fetch("/api/action/list", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          operation: "list",
          query: pendingActionDraft.query,
          params: pendingActionDraft.params || {},
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(formatDetail(data.detail) || "No se pudo cargar la lista.", true);
        appendMessage(
          "assistant",
          formatDetail(data.detail) || "No se pudo obtener la lista solicitada."
        );
        actionModalConfirm.disabled = false;
        return;
      }
      renderListResult(data);
      actionModalConfirm.disabled = false;
      return;
    }
    if (pendingActionDraft.operation === "email") {
      const vals = gatherModalValues();
      const target = vals._target || pendingActionDraft.target || "partner";
      const params = {
        to_name: vals.to_name || "",
        to_email: vals.to_email || "",
        subject: vals.subject || "",
        body: vals.body || "",
        record_id: vals.record_id ? Number(vals.record_id) : 0,
      };
      const res = await fetch("/api/action/email", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operation: "email", target, params }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(formatDetail(data.detail) || "No se pudo enviar el correo.", true);
        appendMessage("assistant", formatDetail(data.detail) || "No se pudo enviar el correo.");
        actionModalConfirm.disabled = false;
        return;
      }
      showToast(`Correo enviado a ${data.to}`);
      appendMessage(
        "assistant",
        `Correo enviado por Odoo (mail.mail id ${data.mail_id}) a ${data.to}. Asunto: «${data.subject}».`
      );
      closeActionModal();
      return;
    }
    if (pendingActionDraft.operation === "workflow") {
      const vals = gatherModalValues();
      const draftParams = pendingActionDraft.params || {};
      const params = {
        partner_name: vals.partner_name || draftParams.partner_name || draftParams.customer_name || "",
        product_name: vals.product_name || draftParams.product_name || "",
        amount:
          vals.amount && Number(vals.amount) > 0
            ? Number(vals.amount)
            : Number(draftParams.amount || draftParams.total || draftParams.amount_total || 0),
        qty:
          vals.qty && Number(vals.qty) > 0
            ? Number(vals.qty)
            : Number(draftParams.qty || 1),
      };
      const res = await fetch("/api/action/workflow", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operation: "workflow", name: pendingActionDraft.name, params }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(formatDetail(data.detail) || "Falló el workflow.", true);
        appendMessage("assistant", formatDetail(data.detail) || "Falló el workflow en Odoo.");
        actionModalConfirm.disabled = false;
        return;
      }
      const steps = Array.isArray(data.steps) ? data.steps : [];
      const lines = [];
      lines.push(
        data.ok
          ? "Workflow ejecutado en Odoo correctamente."
          : "Workflow ejecutado con incidencias. Revisa los pasos:"
      );
      for (const s of steps) {
        lines.push(`- ${s.ok ? "OK" : "FALLÓ"} · ${s.step || "Paso"}: ${s.detail || ""}`);
      }
      const odooLinks = [];
      if (data.partner_id) {
        const u = odooRecordUrl("res.partner", data.partner_id);
        if (u) odooLinks.push({ label: "Cliente en Odoo", url: u });
      }
      if (data.sale_order_id) {
        const u = odooRecordUrl("sale.order", data.sale_order_id);
        if (u) odooLinks.push({ label: "Orden de venta en Odoo", url: u });
      }
      if (data.invoice_id) {
        const u = odooRecordUrl("account.move", data.invoice_id);
        if (u) odooLinks.push({ label: "Factura en Odoo", url: u });
      }
      appendMessage("assistant", lines.join("\n"), { odoo_links: odooLinks });
      showToast(data.ok ? "Workflow completado." : "Workflow completado con incidencias.");
      closeActionModal();
      return;
    }
    if (pendingActionDraft.operation === "product_setup") {
      const res = await fetch("/api/action/product-setup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan: pendingActionDraft.plan }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        showToast(formatDetail(data.detail) || "Falló la configuración en Odoo.", true);
        appendMessage(
          "assistant",
          formatDetail(data.detail) || "No se pudo ejecutar el plan."
        );
        actionModalConfirm.disabled = false;
        return;
      }
      const lines = (data.log || []).join("\n");
      appendMessage(
        "assistant",
        `Configuración aplicada en Odoo (plantilla id ${data.product_tmpl_id}, variante id ${data.product_product_id}).\n${lines}`,
        { odoo_links: data.odoo_links }
      );
      showToast("Producto y reglas aplicadas en Odoo.");
      closeActionModal();
      await refreshHealth();
      return;
    }

    const values = gatherModalValues();
    const res = await fetch("/api/action/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: pendingActionDraft.model,
        operation: "create",
        values,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (tryOfferSuggestedAction(data.detail)) {
        actionModalConfirm.disabled = false;
        return;
      }
      showToast(formatDetail(data.detail) || "Odoo rechazó la creación.", true);
      appendMessage(
        "assistant",
        formatDetail(data.detail) || "No se pudo crear el registro en Odoo."
      );
      actionModalConfirm.disabled = false;
      return;
    }
    showToast(`Creado en Odoo (${pendingActionDraft.model} id=${data.id}).`);
    appendMessage(
      "assistant",
      `Listo: se creó el registro en Odoo (${pendingActionDraft.model}, id ${data.id}). Abrí el formulario desde el enlace inferior.`,
      { odoo_links: data.odoo_links }
    );
    closeActionModal();
    await refreshHealth();
  } catch (err) {
    showToast(String(err), true);
    actionModalConfirm.disabled = false;
  }
}

function renderWorkflowResult(data) {
  actionModalTitle.textContent = "Resultado workflow";
  actionModalLead.textContent = data.ok
    ? "El flujo se ejecutó completo en Odoo."
    : "Algunos pasos fallaron. Revisa los detalles.";
  actionModalForm.innerHTML = "";
  const wrap = document.createElement("div");
  wrap.className = "workflow-steps";
  const steps = Array.isArray(data.steps) ? data.steps : [];
  steps.forEach((s, idx) => {
    const row = document.createElement("div");
    row.className = `workflow-step ${s.ok ? "ok" : "fail"}`;
    const num = document.createElement("div");
    num.className = "step-num";
    num.textContent = String(idx + 1);
    const mid = document.createElement("div");
    const name = document.createElement("div");
    name.className = "step-name";
    name.textContent = s.step || `Paso ${idx + 1}`;
    const det = document.createElement("div");
    det.className = "step-detail";
    det.textContent = s.detail || "";
    mid.appendChild(name);
    mid.appendChild(det);
    const badge = document.createElement("span");
    badge.className = `badge ${s.ok ? "success" : "danger"}`;
    badge.textContent = s.ok ? "OK" : "Falló";
    row.appendChild(num);
    row.appendChild(mid);
    row.appendChild(badge);
    wrap.appendChild(row);
  });
  actionModalForm.appendChild(wrap);
}

function renderDashboardResult(data) {
  actionModalTitle.textContent = data.title || "Dashboard";
  actionModalLead.textContent = "KPIs en tiempo real desde Odoo.";
  actionModalForm.innerHTML = "";
  const k = data.kpis || {};
  const fmt = (n) => Number(n || 0).toLocaleString();

  const grid = document.createElement("div");
  grid.className = "dashboard-grid";
  const cards = [
    { label: "Ventas (mes)", value: fmt(k.sales_month), kind: "accent", sub: "Confirmadas+terminadas" },
    { label: "Facturado (mes)", value: fmt(k.invoiced_month), kind: "success", sub: "Facturas cliente posteadas" },
    { label: "Vencido", value: fmt(k.overdue_amount), kind: "danger", sub: `${fmt(k.overdue_count)} factura(s)` },
    { label: "Compras (mes)", value: fmt(k.purchases_month), kind: "warn", sub: "Órdenes confirmadas" },
    { label: "Cotizaciones", value: fmt(k.open_quotations), kind: "", sub: "Borrador / enviadas" },
    { label: "Órdenes confirmadas", value: fmt(k.confirmed_orders), kind: "accent", sub: "Sale orders activas" },
    { label: "Entregas pendientes", value: fmt(k.pickings_pending), kind: "warn", sub: "Pickings asignados/pendientes" },
    { label: "Borradores factura", value: fmt(k.draft_invoices), kind: "", sub: "Por revisar" },
    { label: "Clientes", value: fmt(k.customers), kind: "", sub: "Total activos" },
    { label: "Proveedores", value: fmt(k.vendors), kind: "", sub: "Total activos" },
    { label: "Productos", value: fmt(k.products), kind: "", sub: "Activos" },
  ];
  cards.forEach((c) => {
    const card = document.createElement("div");
    card.className = `kpi-card ${c.kind}`;
    const lab = document.createElement("div"); lab.className = "kpi-label"; lab.textContent = c.label;
    const val = document.createElement("div"); val.className = "kpi-value"; val.textContent = c.value;
    const sub = document.createElement("div"); sub.className = "kpi-sub"; sub.textContent = c.sub;
    card.appendChild(lab);
    card.appendChild(val);
    card.appendChild(sub);
    grid.appendChild(card);
  });
  actionModalForm.appendChild(grid);

  const tops = Array.isArray(data.top_customers) ? data.top_customers : [];
  if (tops.length) {
    const sect = document.createElement("div");
    sect.className = "dashboard-section";
    const h = document.createElement("h4");
    h.textContent = "Top clientes facturados (mes)";
    sect.appendChild(h);
    const max = tops.reduce((m, x) => Math.max(m, Number(x.amount || 0)), 0) || 1;
    tops.forEach((t) => {
      const row = document.createElement("div");
      row.className = "bar-row";
      const name = document.createElement("div"); name.className = "bar-name"; name.textContent = t.name || "—";
      const track = document.createElement("div"); track.className = "bar-track";
      const fill = document.createElement("div"); fill.className = "bar-fill";
      fill.style.width = `${Math.max(2, (Number(t.amount || 0) / max) * 100)}%`;
      track.appendChild(fill);
      const amt = document.createElement("div"); amt.className = "bar-amount"; amt.textContent = fmt(t.amount);
      row.appendChild(name);
      row.appendChild(track);
      row.appendChild(amt);
      sect.appendChild(row);
    });
    actionModalForm.appendChild(sect);
  }

  const states = Array.isArray(data.sales_by_state) ? data.sales_by_state : [];
  if (states.length) {
    const sect = document.createElement("div");
    sect.className = "dashboard-section";
    const h = document.createElement("h4");
    h.textContent = "Ventas por estado (mes)";
    sect.appendChild(h);
    const stateMap = (s) => ({
      draft: "Cotización borrador", sent: "Cotización enviada",
      sale: "Venta confirmada", done: "Bloqueada/cerrada", cancel: "Cancelada",
    }[String(s || "").toLowerCase()] || s);
    const max = states.reduce((m, x) => Math.max(m, Number(x.amount || 0)), 0) || 1;
    states.forEach((t) => {
      const row = document.createElement("div");
      row.className = "bar-row";
      const name = document.createElement("div"); name.className = "bar-name"; name.textContent = `${stateMap(t.state)} (${t.count})`;
      const track = document.createElement("div"); track.className = "bar-track";
      const fill = document.createElement("div"); fill.className = "bar-fill";
      fill.style.width = `${Math.max(2, (Number(t.amount || 0) / max) * 100)}%`;
      track.appendChild(fill);
      const amt = document.createElement("div"); amt.className = "bar-amount"; amt.textContent = fmt(t.amount);
      row.appendChild(name);
      row.appendChild(track);
      row.appendChild(amt);
      sect.appendChild(row);
    });
    actionModalForm.appendChild(sect);
  }
}

function renderErpReadResult(data) {
  actionModalTitle.textContent = data.title || "Consulta ERP";
  const meta = data.meta || {};
  actionModalLead.textContent = `Modelo ${meta.model || ""} · ${Number(data.count || 0)} filas · límite ${meta.limit ?? "—"}`;
  actionModalForm.innerHTML = "";
  const items = Array.isArray(data.items) ? data.items : [];
  const fields = Array.isArray(data.fields) ? data.fields : [];
  if (!items.length) {
    const box = document.createElement("div");
    box.className = "modal-plan-summary";
    const msg = document.createElement("div");
    msg.textContent = data.hint || "La consulta no devolvió filas.";
    box.appendChild(msg);
    actionModalForm.appendChild(box);
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
  actionModalForm.appendChild(table);
}

function renderListResult(data) {
  if (data.query === "dashboard_overview") {
    renderDashboardResult(data);
    return;
  }
  if (data.query === "erp_read") {
    renderErpReadResult(data);
    return;
  }
  actionModalTitle.textContent = data.title || "Lista";
  actionModalLead.textContent = `Total: ${Number(data.count || 0)} registros.`;
  actionModalForm.innerHTML = "";
  const items = Array.isArray(data.items) ? data.items : [];
  const box = document.createElement("div");
  box.className = "modal-plan-summary";
  if (!items.length) {
    const emptyMsg =
      data.query === "users_roles"
        ? "No se encontraron usuarios."
        : data.query === "accounting_recent_actions"
          ? "No se encontraron acciones recientes en facturación."
          : data.query === "accounting_missing_key_data"
            ? "No se detectaron facturas con datos clave faltantes."
            : data.query === "users_last_login"
              ? "No se encontraron usuarios para revisar conexión."
              : data.query === "dirty_data_overview"
                ? "No se detectaron datos sucios con estas reglas."
                : data.query === "invoice_from_order_check"
                  ? "No encontré facturas para esa orden."
                  : data.query === "overdue_invoices"
                    ? "No hay facturas vencidas."
                    : data.query === "low_stock_products"
                      ? "No hay productos bajo mínimo."
                      : data.query === "demand_forecast_purchase_hints"
                        ? "No hay ventas recientes suficientes para proyectar demanda."
                        : data.query === "erp_read"
                          ? "La consulta no devolvió filas."
                          : data.query === "best_vendor_for_product"
                        ? "No hay proveedores configurados para ese producto."
                        : data.query === "payroll_preview"
                          ? "No hay datos para cálculo de nómina."
                          : data.query === "latest_product"
                            ? "No hay productos registrados."
                            : data.query === "sales_quarter_compare"
                              ? "No hay ventas para comparar en esos periodos."
                              : data.query === "customers_drop_with_active_contracts"
                                ? "No hay clientes con caída >20% que cumplan los filtros."
                              : data.query === "sales_last_month_total"
                                ? "No hay ventas confirmadas para el último mes."
                              : data.query === "issued_invoices_month_total"
                                ? "No hay facturas emitidas en el mes actual."
        : "No hay órdenes pendientes por entregar.";
    const msg = document.createElement("div");
    msg.textContent = data.hint || emptyMsg;
    box.appendChild(msg);
    if (
      data.query === "invoice_from_order_check" &&
      data.suggested_action &&
      data.suggested_action.operation === "create"
    ) {
      const ctaWrap = document.createElement("div");
      ctaWrap.style.marginTop = "12px";
      const cta = document.createElement("button");
      cta.type = "button";
      cta.className = "btn primary";
      cta.textContent = "Crear factura para esta orden";
      cta.addEventListener("click", () => {
        const draft = data.suggested_action;
        closeActionModal();
        openActionModal(draft);
      });
      ctaWrap.appendChild(cta);
      box.appendChild(ctaWrap);
    }
    actionModalForm.appendChild(box);
    return;
  }
  const table = document.createElement("table");
  table.className = "modal-table";
  const thead = document.createElement("thead");
  if (data.query === "users_roles") {
    thead.innerHTML =
      "<tr><th>Usuario</th><th>Login</th><th>Activo</th><th>Tipo</th><th>Roles / grupos</th></tr>";
  } else if (data.query === "accounting_recent_actions") {
    thead.innerHTML =
      "<tr><th>Documento</th><th>Tipo</th><th>Estado</th><th>Cliente/Proveedor</th><th>Última actualización</th><th class='num'>Total</th><th>Pago</th></tr>";
  } else if (data.query === "accounting_missing_key_data") {
    thead.innerHTML =
      "<tr><th>Documento</th><th>Tipo</th><th>Estado</th><th>Cliente/Proveedor</th><th>Fecha</th><th>Vencimiento</th><th>Moneda</th><th>Campos faltantes</th></tr>";
  } else if (data.query === "users_last_login") {
    thead.innerHTML =
      "<tr><th>Usuario</th><th>Login</th><th>Activo</th><th>Última conexión</th></tr>";
  } else if (data.query === "dirty_data_overview") {
    thead.innerHTML =
      "<tr><th>Entidad</th><th>Registro</th><th>Problemas detectados</th></tr>";
  } else if (data.query === "invoice_from_order_check") {
    thead.innerHTML =
      "<tr><th>Documento</th><th>Orden</th><th>Cliente</th><th>Estado</th><th class='num'>Total</th><th>Control duplicado</th></tr>";
  } else if (data.query === "overdue_invoices") {
    thead.innerHTML =
      "<tr><th>Documento</th><th>Cliente</th><th>Vencimiento</th><th class='num'>Saldo</th><th>Estado de pago</th></tr>";
  } else if (data.query === "low_stock_products") {
    thead.innerHTML =
      "<tr><th>Producto</th><th class='num'>Mínimo</th><th class='num'>Máximo</th><th class='num'>Sugerido</th><th>Acción</th></tr>";
  } else if (data.query === "demand_forecast_purchase_hints") {
    thead.innerHTML =
      "<tr><th>Producto</th><th class='num'>Vendido 90 d</th><th class='num'>Prom. mensual</th><th class='num'>Tend. %</th><th class='num'>Pronóstico periodo</th><th>Sugerencia compras</th></tr>";
  } else if (data.query === "best_vendor_for_product") {
    thead.innerHTML =
      "<tr><th>Proveedor</th><th class='num'>Precio</th><th class='num'>Cant. mínima</th><th class='num'>Lead time (días)</th><th>Mejor opción</th></tr>";
  } else if (data.query === "payroll_preview") {
    thead.innerHTML =
      "<tr><th>Empleado</th><th class='num'>Sueldo base</th><th class='num'>Horas extra</th><th class='num'>Bono</th><th class='num'>Pago extra</th><th class='num'>Total</th><th>Nota</th></tr>";
  } else if (data.query === "latest_product") {
    thead.innerHTML =
      "<tr><th>Producto</th><th>Referencia</th><th>Creado</th><th class='num'>Precio venta</th><th class='num'>Costo</th><th>Activo</th></tr>";
  } else if (data.query === "sales_quarter_compare") {
    thead.innerHTML =
      "<tr><th>Región</th><th>Canal</th><th class='num'>Ventas trimestre actual</th><th class='num'>Mismo trimestre año pasado</th><th class='num'>Δ</th><th class='num'>Crecimiento %</th><th class='num'>Margen neto est.</th><th class='num'>Margen %</th></tr>";
  } else if (data.query === "customers_drop_with_active_contracts") {
    thead.innerHTML =
      "<tr><th>Cliente</th><th class='num'>Mes -2</th><th class='num'>Mes -1</th><th class='num'>Caída %</th><th>Contrato activo</th><th>Incidencias facturación</th></tr>";
  } else if (data.query === "sales_last_month_total") {
    thead.innerHTML =
      "<tr><th>Periodo</th><th class='num'>Total ventas</th></tr>";
  } else if (data.query === "issued_invoices_month_total") {
    thead.innerHTML =
      "<tr><th>Periodo</th><th class='num'>Facturas emitidas</th><th class='num'>Suma total</th></tr>";
  } else {
    thead.innerHTML =
      "<tr><th>Pedido</th><th>Cliente</th><th>Fecha</th><th class='num'>Total</th><th>Entrega</th><th>Factura</th></tr>";
  }
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  const mapMoveType = (value) => {
    const v = String(value || "").toLowerCase();
    if (v === "out_invoice") return "Factura cliente";
    if (v === "in_invoice") return "Factura proveedor";
    if (v === "entry") return "Asiento contable";
    if (v === "out_refund") return "Nota de crédito cliente";
    if (v === "in_refund") return "Nota de crédito proveedor";
    return value || "";
  };
  const mapState = (value) => {
    const v = String(value || "").toLowerCase();
    if (v === "draft") return "Borrador";
    if (v === "posted") return "Publicado";
    if (v === "cancel") return "Cancelado";
    return value || "";
  };
  const mapPaymentState = (value) => {
    const v = String(value || "").toLowerCase();
    if (v === "not_paid") return "No pagado";
    if (v === "paid") return "Pagado";
    if (v === "partial") return "Pago parcial";
    if (v === "in_payment") return "En pago";
    if (v === "reversed") return "Revertido";
    return value || "";
  };
  const mapDeliveryStatus = (value) => {
    const v = String(value || "").toLowerCase();
    if (v === "pending") return "Pendiente";
    if (v === "partially") return "Parcial";
    if (v === "full") return "Completa";
    return value || "";
  };
  const mapInvoiceStatus = (value) => {
    const v = String(value || "").toLowerCase();
    if (v === "to invoice") return "Por facturar";
    if (v === "upselling") return "Venta adicional";
    if (v === "invoiced") return "Facturada";
    if (v === "no") return "Nada que facturar";
    return value || "";
  };
  if (data.query === "users_roles") {
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.name || ""}</td>
        <td>${it.login || ""}</td>
        <td>${it.active ? "Sí" : "No"}</td>
        <td>${it.internal_user ? "Interno" : "Portal/Compartido"}</td>
        <td>${it.roles || "—"}</td>`;
      tbody.appendChild(tr);
    }
  } else if (data.query === "accounting_recent_actions") {
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.name || ""}</td>
        <td>${mapMoveType(it.move_type)}</td>
        <td>${mapState(it.state)}</td>
        <td>${it.partner || ""}</td>
        <td>${(it.write_date || "").replace("T", " ").slice(0, 19)}</td>
        <td class="num">${Number(it.amount_total || 0).toLocaleString()}</td>
        <td>${mapPaymentState(it.payment_state)}</td>`;
      tbody.appendChild(tr);
    }
  } else if (data.query === "accounting_missing_key_data") {
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.name || ""}</td>
        <td>${mapMoveType(it.move_type)}</td>
        <td>${mapState(it.state)}</td>
        <td>${it.partner || ""}</td>
        <td>${it.invoice_date || ""}</td>
        <td>${it.invoice_date_due || ""}</td>
        <td>${it.currency || ""}</td>
        <td>${it.missing_fields || ""}</td>`;
      tbody.appendChild(tr);
    }
  } else if (data.query === "users_last_login") {
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.name || ""}</td>
        <td>${it.login || ""}</td>
        <td>${it.active ? "Sí" : "No"}</td>
        <td>${it.last_login ? String(it.last_login).replace("T", " ").slice(0, 19) : "Sin registro"}</td>`;
      tbody.appendChild(tr);
    }
  } else if (data.query === "dirty_data_overview") {
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.entity || ""}</td>
        <td>${it.record || ""}</td>
        <td>${it.issues || ""}</td>`;
      tbody.appendChild(tr);
    }
  } else if (data.query === "invoice_from_order_check") {
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.document || ""}</td>
        <td>${it.order_ref || ""}</td>
        <td>${it.partner || ""}</td>
        <td>${mapState(it.state)}</td>
        <td class="num">${Number(it.amount_total || 0).toLocaleString()}</td>
        <td>${it.duplicate_flag || ""}</td>`;
      tbody.appendChild(tr);
    }
  } else if (data.query === "overdue_invoices") {
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.document || ""}</td>
        <td>${it.partner || ""}</td>
        <td>${it.due_date || ""}</td>
        <td class="num">${Number(it.residual || 0).toLocaleString()}</td>
        <td>${mapPaymentState(it.payment_state)}</td>`;
      tbody.appendChild(tr);
    }
  } else if (data.query === "low_stock_products") {
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.product || ""}</td>
        <td class="num">${Number(it.min_qty || 0).toLocaleString()}</td>
        <td class="num">${Number(it.max_qty || 0).toLocaleString()}</td>
        <td class="num">${Number(it.suggested_qty || 0).toLocaleString()}</td>
        <td>${it.suggested_action || ""}</td>`;
      tbody.appendChild(tr);
    }
  } else if (data.query === "demand_forecast_purchase_hints") {
    if (data.meta && data.meta.horizon_months != null) {
      actionModalLead.textContent = `Ventana ${Number(data.meta.window_days || 90)} días · horizonte ${Number(data.meta.horizon_months)} mes(es).`;
    }
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.product || ""}</td>
        <td class="num">${Number(it.sold_qty_90d || 0).toLocaleString()}</td>
        <td class="num">${Number(it.avg_monthly || 0).toLocaleString()}</td>
        <td class="num">${Number(it.trend_pct || 0).toLocaleString()}%</td>
        <td class="num">${Number(it.forecast_horizon_qty || 0).toLocaleString()}</td>
        <td>${it.purchase_hint || ""}</td>`;
      tbody.appendChild(tr);
    }
  } else if (data.query === "best_vendor_for_product") {
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.vendor || ""}</td>
        <td class="num">${Number(it.price || 0).toLocaleString()}</td>
        <td class="num">${Number(it.min_qty || 0).toLocaleString()}</td>
        <td class="num">${Number(it.lead_days || 0).toLocaleString()}</td>
        <td>${it.best_option || ""}</td>`;
      tbody.appendChild(tr);
    }
  } else if (data.query === "payroll_preview") {
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.employee || ""}</td>
        <td class="num">${Number(it.base_salary || 0).toLocaleString()}</td>
        <td class="num">${Number(it.hours_extra || 0).toLocaleString()}</td>
        <td class="num">${Number(it.bonus || 0).toLocaleString()}</td>
        <td class="num">${Number(it.extra_pay || 0).toLocaleString()}</td>
        <td class="num">${Number(it.total || 0).toLocaleString()}</td>
        <td>${it.message || ""}</td>`;
      tbody.appendChild(tr);
    }
  } else if (data.query === "latest_product") {
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.name || ""}</td>
        <td>${it.default_code || ""}</td>
        <td>${(it.create_date || "").replace("T", " ").slice(0, 19)}</td>
        <td class="num">${Number(it.list_price || 0).toLocaleString()}</td>
        <td class="num">${Number(it.standard_price || 0).toLocaleString()}</td>
        <td>${it.active ? "Sí" : "No"}</td>`;
      tbody.appendChild(tr);
    }
  } else if (data.query === "sales_quarter_compare") {
    if (data.meta && data.meta.current_period && data.meta.previous_period) {
      actionModalLead.textContent = `Comparando ${data.meta.current_period} vs ${data.meta.previous_period} · costo logístico variable ${(Number(data.meta.logistic_rate || 0) * 100).toFixed(1)}%`;
    }
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.region || ""}</td>
        <td>${it.channel || ""}</td>
        <td class="num">${Number(it.sales_current_quarter || 0).toLocaleString()}</td>
        <td class="num">${Number(it.sales_same_quarter_last_year || 0).toLocaleString()}</td>
        <td class="num">${Number(it.delta || 0).toLocaleString()}</td>
        <td class="num">${Number(it.growth_pct || 0).toLocaleString()}%</td>
        <td class="num">${Number(it.net_margin_estimated || 0).toLocaleString()}</td>
        <td class="num">${Number(it.net_margin_pct || 0).toLocaleString()}%</td>`;
      tbody.appendChild(tr);
    }
  } else if (data.query === "customers_drop_with_active_contracts") {
    if (data.meta && data.meta.month_prev2 && data.meta.month_prev1) {
      actionModalLead.textContent = `Comparación mensual ${data.meta.month_prev2} vs ${data.meta.month_prev1} · umbral caída ${Number(data.meta.drop_threshold_pct || 20).toFixed(1)}%`;
    }
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.customer || ""}</td>
        <td class="num">${Number(it.month_prev2_sales || 0).toLocaleString()}</td>
        <td class="num">${Number(it.month_prev1_sales || 0).toLocaleString()}</td>
        <td class="num">${Number(it.drop_pct || 0).toLocaleString()}%</td>
        <td>${it.has_active_contract ? "Sí" : "No"}</td>
        <td>${Number(it.billing_incidents || 0).toLocaleString()}</td>`;
      tbody.appendChild(tr);
    }
  } else if (data.query === "sales_last_month_total") {
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.period || ""}</td>
        <td class="num">${Number(it.sales_total || 0).toLocaleString()}</td>`;
      tbody.appendChild(tr);
    }
  } else if (data.query === "issued_invoices_month_total") {
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.period || ""}</td>
        <td class="num">${Number(it.invoices_count || 0).toLocaleString()}</td>
        <td class="num">${Number(it.invoices_total || 0).toLocaleString()}</td>`;
      tbody.appendChild(tr);
    }
  } else {
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.name || ""}</td>
        <td>${it.customer || ""}</td>
        <td>${(it.date_order || "").slice(0, 10)}</td>
        <td class="num">${Number(it.amount_total || 0).toLocaleString()}</td>
        <td>${mapDeliveryStatus(it.delivery_status)}</td>
        <td>${mapInvoiceStatus(it.invoice_status)}</td>`;
      tbody.appendChild(tr);
    }
  }
  table.appendChild(tbody);
  attachListRowLinks({ query: data.query, items, tbody });
  box.appendChild(table);
  actionModalForm.appendChild(box);
}

actionModalCancel.addEventListener("click", () => closeActionModal());
actionModalConfirm.addEventListener("click", () => confirmActionInsert());

actionModal.addEventListener("click", (e) => {
  if (e.target === actionModal) closeActionModal();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !actionModal.hidden) {
    e.preventDefault();
    closeActionModal();
  }
});

function applyModuleSelection(moduleId, label) {
  document.querySelectorAll(".module-item").forEach((b) => b.classList.toggle("active", b.dataset.module === moduleId));
  const crumb = document.getElementById("moduleCrumb");
  if (crumb && label) crumb.textContent = label;
}

document.querySelectorAll(".module-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    const moduleId = btn.dataset.module;
    const prompt = btn.dataset.prompt || "";
    applyModuleSelection(moduleId, btn.textContent.trim());
    if (prompt) {
      inputEl.value = prompt;
      autoResizeComposer();
      inputEl.focus();
    }
  });
});

document.querySelectorAll(".quick-prompt").forEach((btn) => {
  btn.addEventListener("click", () => {
    const p = btn.dataset.prompt || "";
    if (!p) return;
    inputEl.value = p;
    autoResizeComposer();
    inputEl.focus();
  });
});

refreshHealth();
resetComposerHeight();
inputEl.focus();

/* ================================================================== *
 *  Extensiones v0.3 — alertas, reporte mensual, autocompletar copiloto
 * ================================================================== */
(function copilotExtensions() {
  const alertsListEl = document.getElementById("alertsList");
  const btnAlertsRefresh = document.getElementById("btnAlertsRefresh");
  const btnReport = document.getElementById("btnReport");
  const suggestionsEl = document.getElementById("suggestions");

  const ALERT_PROMPT = {
    low_stock: "Revisa productos bajo mínimo de stock",
    overdue_invoices: "Muéstrame las facturas vencidas",
    stale_drafts: "Muéstrame las facturas en borrador antiguas",
  };

  async function loadAlerts({ force = false } = {}) {
    if (!alertsListEl) return;
    alertsListEl.innerHTML = '<span class="hint subtle">Cargando…</span>';
    try {
      const url = force ? "/api/alerts/run" : "/api/alerts";
      const opts = force
        ? {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ use_cache: false }),
          }
        : {};
      const res = await fetch(url, opts);
      const data = await res.json();
      if (!res.ok || !data?.alerts) {
        alertsListEl.innerHTML =
          '<span class="hint subtle">No se pudo cargar.</span>';
        return;
      }
      renderAlerts(data.alerts);
    } catch (err) {
      alertsListEl.innerHTML = `<span class="hint subtle">${String(err)}</span>`;
    }
  }

  function renderAlerts(alerts) {
    alertsListEl.innerHTML = "";
    if (!alerts.length) {
      alertsListEl.innerHTML =
        '<span class="hint subtle">Sin alertas configuradas.</span>';
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
        inputEl.value = prompt;
        if (typeof autoResizeComposer === "function") autoResizeComposer();
        inputEl.focus();
      });
      alertsListEl.appendChild(btn);
    }
  }

  if (btnAlertsRefresh) {
    btnAlertsRefresh.addEventListener("click", () => loadAlerts({ force: true }));
  }

  if (btnReport) {
    btnReport.addEventListener("click", async () => {
      btnReport.disabled = true;
      const originalLabel = btnReport.textContent;
      btnReport.textContent = "Generando reporte…";
      try {
        const res = await fetch("/api/report/sales", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ write_summary: true }),
        });
        const data = await res.json();
        if (!res.ok) {
          showToast(formatDetail(data?.detail) || "No se pudo generar el reporte.", true);
          return;
        }
        renderReport(data);
      } catch (err) {
        showToast(String(err), true);
      } finally {
        btnReport.disabled = false;
        btnReport.textContent = originalLabel;
      }
    });
  }

  function renderReport(payload) {
    const totals = payload?.data?.totals || {};
    const period = payload?.data?.period || {};
    const top = payload?.data?.top_customers || [];
    const lines = [];
    lines.push(`Reporte de ventas (${period.label || "mes"})`);
    lines.push(
      `• Ventas: ${fmtMoney(totals.sales_amount)}  |  Mes anterior: ${fmtMoney(totals.previous_month_sales)}` +
        (totals.growth_pct != null ? `  |  Crec.: ${totals.growth_pct.toFixed(2)}%` : ""),
    );
    lines.push(
      `• Facturado: ${fmtMoney(totals.invoiced_amount)}  |  Órdenes confirmadas: ${totals.confirmed_orders ?? 0}`,
    );
    if (top.length) {
      lines.push("• Top clientes:");
      for (const c of top.slice(0, 5)) {
        lines.push(`   - ${c.name}: ${fmtMoney(c.amount)}`);
      }
    }
    if (payload?.summary) {
      lines.push("\nAnálisis:");
      lines.push(payload.summary);
    }
    appendAssistantMessage(lines.join("\n"));
  }

  function appendAssistantMessage(text) {
    if (typeof appendMessage === "function") {
      appendMessage("assistant", text);
      return;
    }
    if (!messagesEl) return;
    const el = document.createElement("div");
    el.className = "msg assistant";
    el.textContent = text;
    messagesEl.appendChild(el);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function fmtMoney(value) {
    const n = Number(value || 0);
    if (!isFinite(n)) return "0";
    return n.toLocaleString("es-CL", { maximumFractionDigits: 0 });
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[c]));
  }

  /* ─────────── Autocompletar (modo copiloto) ─────────── */

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

  function detectTrigger(text) {
    if (!text) return null;
    for (const t of TRIGGER_PATTERNS) {
      const m = text.match(t.regex);
      if (m && m[1] && m[1].trim().length >= 2) {
        return { kind: t.kind, query: m[1].trim(), matched: m[0] };
      }
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

  function renderSuggestions(items, kind) {
    if (!suggestionsEl) return;
    if (!items.length) {
      hideSuggestions();
      return;
    }
    activeIndex = -1;
    currentItems = items;
    suggestionsEl.innerHTML = "";
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
      suggestionsEl.appendChild(node);
    });
    suggestionsEl.hidden = false;
  }

  function hideSuggestions() {
    if (!suggestionsEl) return;
    suggestionsEl.hidden = true;
    suggestionsEl.innerHTML = "";
    activeIndex = -1;
    currentItems = [];
    currentMatch = null;
  }

  function applySuggestion(idx) {
    if (!currentMatch || idx < 0 || idx >= currentItems.length) {
      hideSuggestions();
      return;
    }
    const choice = currentItems[idx];
    const text = inputEl.value;
    const start = text.length - currentMatch.matched.length;
    if (start < 0) {
      hideSuggestions();
      return;
    }
    const prefix = text.slice(0, start);
    // mantenemos el verbo original (e.g. "para") y reemplazamos sólo el nombre
    const verbMatch = currentMatch.matched.match(/^(\S+)\s+/);
    const verb = verbMatch ? verbMatch[1] + " " : "";
    inputEl.value = prefix + verb + choice.label + " ";
    if (typeof autoResizeComposer === "function") autoResizeComposer();
    inputEl.focus();
    hideSuggestions();
  }

  function highlightActive() {
    if (!suggestionsEl) return;
    const nodes = suggestionsEl.querySelectorAll(".suggestion");
    nodes.forEach((n, i) => n.classList.toggle("active", i === activeIndex));
  }

  if (inputEl) {
    inputEl.addEventListener("input", () => {
      const text = inputEl.value;
      const match = detectTrigger(text);
      if (!match) {
        hideSuggestions();
        return;
      }
      currentMatch = match;
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(async () => {
        const items = await fetchSuggestions(match.kind, match.query);
        if (currentMatch === match) renderSuggestions(items, match.kind);
      }, 180);
    });

    inputEl.addEventListener("keydown", (ev) => {
      if (suggestionsEl?.hidden) return;
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

    inputEl.addEventListener("blur", () => {
      setTimeout(hideSuggestions, 120);
    });
  }

  // Carga inicial de alertas (sin bloquear si el backend tarda).
  setTimeout(() => loadAlerts({ force: false }), 600);
})();
