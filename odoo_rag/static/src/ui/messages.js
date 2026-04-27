import { dom } from "../dom.js";

export function appendMessage(role, text, options = {}) {
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
  if (role === "assistant" && Array.isArray(links) && links.length > 0) {
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

  dom.messagesEl.appendChild(wrap);
  dom.messagesEl.scrollTop = dom.messagesEl.scrollHeight;
  return wrap;
}

