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

function openActionModal(draft) {
  pendingActionDraft = draft;
  actionModalTitle.textContent = draft.summary || "Confirmar inserción";
  actionModalForm.innerHTML = "";
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
    if (pendingActionDraft.operation === "list" && pendingActionDraft.query) {
      const res = await fetch("/api/action/list", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          operation: "list",
          query: pendingActionDraft.query,
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

function renderListResult(data) {
  actionModalTitle.textContent = data.title || "Lista";
  actionModalLead.textContent = `Total: ${Number(data.count || 0)} registros.`;
  actionModalForm.innerHTML = "";
  const items = Array.isArray(data.items) ? data.items : [];
  const box = document.createElement("div");
  box.className = "modal-plan-summary";
  if (!items.length) {
    box.textContent =
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
        : "No hay órdenes pendientes por entregar.";
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
  } else {
    for (const it of items) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${it.name || ""}</td>
        <td>${it.customer || ""}</td>
        <td>${(it.date_order || "").slice(0, 10)}</td>
        <td class="num">${Number(it.amount_total || 0).toLocaleString()}</td>
        <td>${it.delivery_status || ""}</td>
        <td>${it.invoice_status || ""}</td>`;
      tbody.appendChild(tr);
    }
  }
  table.appendChild(tbody);
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

refreshHealth();
resetComposerHeight();
inputEl.focus();
