export async function apiFetchJson(url, opts = {}) {
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, statusText: res.statusText, data };
}

export function formatDetail(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((x) => (x && x.msg ? x.msg : JSON.stringify(x))).join(" ");
  }
  try {
    return JSON.stringify(detail);
  } catch {
    return "";
  }
}

