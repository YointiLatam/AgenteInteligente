// Pure helpers: no DOM access, so they can be tested with `node --test`.

export const CHANNEL_LABELS = { web_widget: "Widget web", whatsapp: "WhatsApp", meta: "Meta" };
export const STATUS_LABELS = { processing: "En proceso", completed: "Completada", failed: "Fallida" };
export const DECISION_LABELS = { approved: "Aprobada", rejected: "Rechazada" };
export const ROLE_LABELS = { admin: "Administrador", operator: "Operador" };

const TABS = [
  { id: "approvals", label: "Aprobaciones", roles: ["operator", "admin"] },
  { id: "conversations", label: "Conversaciones", roles: ["operator", "admin"] },
  { id: "audit", label: "Auditoría", roles: ["admin"] },
];

/** Tabs the role may see. The server remains the enforcer. */
export function tabsForRole(role) {
  return TABS.filter((tab) => tab.roles.includes(role));
}

export function canView(role, tabId) {
  return tabsForRole(role).some((tab) => tab.id === tabId);
}

export function label(map, value) {
  return Object.hasOwn(map, value) ? map[value] : String(value ?? "—");
}

/** Pretty JSON for tool parameters; the result must be inserted with textContent. */
export function formatParams(params) {
  try {
    return JSON.stringify(params ?? {}, null, 2) ?? "{}";
  } catch {
    return "[parámetros no legibles]";
  }
}

export function formatDate(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("es", { dateStyle: "medium", timeStyle: "short" });
}

/** Pagination math. `page` is 1-based; `pages` is at least 1. */
export function pageInfo(total, limit, offset) {
  const safeLimit = Math.max(1, limit | 0);
  const pages = Math.max(1, Math.ceil(Math.max(0, total) / safeLimit));
  const page = Math.min(pages, Math.floor(Math.max(0, offset) / safeLimit) + 1);
  return {
    page,
    pages,
    hasPrev: offset > 0,
    hasNext: offset + safeLimit < total,
    prevOffset: Math.max(0, offset - safeLimit),
    nextOffset: offset + safeLimit,
  };
}

export function pageLabel(total, limit, offset) {
  const { page, pages } = pageInfo(total, limit, offset);
  return `Página ${page} de ${pages} (${total} en total)`;
}

/** Query string without empty values. */
export function buildQuery(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && String(value).trim() !== "") {
      search.set(key, String(value).trim());
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

/** Maps an HTTP status to a user-facing message and the action the UI must take. */
export function errorInfo(status) {
  switch (status) {
    case 401:
      return { kind: "unauthorized", message: "Tu sesión no es válida o expiró. Inicia sesión de nuevo." };
    case 403:
      return { kind: "forbidden", message: "Sin permiso para esta acción." };
    case 404:
      return { kind: "notfound", message: "No se encontró el recurso." };
    case 409:
      return { kind: "conflict", message: "La acción ya no está pendiente o ya tiene otra decisión. Actualiza la lista." };
    case 422:
      return { kind: "invalid", message: "Los datos enviados no son válidos." };
    case 502:
      return { kind: "upstream", message: "La herramienta aprobada falló al ejecutarse." };
    case 503:
      return { kind: "unavailable", message: "El servicio no está disponible. Inténtalo más tarde." };
    default:
      return { kind: "unknown", message: "Ocurrió un error inesperado." };
  }
}

export function decisionSummary(decision) {
  return decision === "approved"
    ? "Vas a aprobar esta acción: la herramienta se ejecutará."
    : "Vas a rechazar esta acción: la herramienta no se ejecutará.";
}

export function decisionBody(item, decision, reason) {
  const body = {
    thread_id: item.thread_id,
    turn_id: item.turn_id,
    tool_name: item.tool_name,
    decision,
  };
  const trimmed = (reason ?? "").trim();
  if (trimmed) body.reason = trimmed;
  return body;
}
