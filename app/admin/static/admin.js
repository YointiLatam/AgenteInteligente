// Panel entry point. All dynamic data is rendered with textContent / DOM APIs (never innerHTML).
import { createApi } from "./api.js";
import {
  CHANNEL_LABELS, DECISION_LABELS, ROLE_LABELS, STATUS_LABELS, buildQuery, canView, decisionBody,
  decisionSummary, errorInfo, formatDate, formatParams, label, pageInfo, pageLabel, tabsForRole,
} from "./render.js";
import { PAGE_SIZE, POLL_MS, createState } from "./state.js";

const $ = (id) => document.getElementById(id);
const state = createState();
const api = createApi({ onUnauthorized: () => showLogin("Tu sesión expiró. Inicia sesión de nuevo.") });

let pollTimer = null;
let confirmContext = null;
let lastFocus = null;
const seq = { approvals: 0, conv: 0, messages: 0, audit: 0 };

function el(tag, { text, className, attrs } = {}, children = []) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  for (const [key, value] of Object.entries(attrs ?? {})) node.setAttribute(key, value);
  for (const child of children) node.append(child);
  return node;
}

function setStatus(message, kind = "") {
  const node = $("status");
  node.textContent = message;
  node.className = `status ${kind}`.trim();
}

function setBusy(button, busy) {
  button.disabled = busy;
  if (busy) button.setAttribute("aria-busy", "true");
  else button.removeAttribute("aria-busy");
}

/** Shared error handling: 401 is handled by the api wrapper; 403 shows the "sin permiso" view. */
function handleError(error, { fallbackView = true } = {}) {
  const info = error && error.kind ? error : { ...errorInfo(0), message: "No se pudo conectar con el servidor." };
  if (info.kind === "unauthorized") return;
  if (info.kind === "forbidden" && fallbackView) {
    showView("denied");
    $("denied-title").focus();
    setStatus("Sin permiso para esta acción.", "error");
    return;
  }
  setStatus(info.message, "error");
}

function showView(name) {
  for (const id of ["approvals", "conversations", "messages", "audit", "denied"]) {
    $(`view-${id}`).hidden = id !== name;
  }
}

function setPager(id, total, limit, offset) {
  const pager = $(id);
  const info = pageInfo(total, limit, offset);
  pager.querySelector(".pager-info").textContent = pageLabel(total, limit, offset);
  pager.querySelector('[data-dir="prev"]').disabled = !info.hasPrev;
  pager.querySelector('[data-dir="next"]').disabled = !info.hasNext;
  pager.hidden = total === 0;
}

// ---- session -------------------------------------------------------------------------------

function showLogin(message = "") {
  stopPolling();
  api.logout();
  state.user = null;
  state.tab = null;
  closeConfirm();
  $("app").hidden = true;
  $("session").hidden = true;
  $("view-login").hidden = false;
  $("api-key").value = "";
  setBusy($("login-submit"), false);
  // Clear any rendered data so nothing remains after logout / 401.
  for (const id of ["approvals-list", "conv-body", "messages-list", "audit-body"]) $(id).replaceChildren();
  setStatus(message, message ? "error" : "");
  $("api-key").focus();
}

async function onLogin(event) {
  event.preventDefault();
  const input = $("api-key");
  const key = input.value.trim();
  if (!key) return;
  const submit = $("login-submit");
  setBusy(submit, true);
  try {
    await api.login(key);
    input.value = "";
    const me = await api.get("/admin/me");
    state.user = me;
    startSession();
  } catch (error) {
    input.value = "";
    api.logout();
    const message =
      error && error.kind === "unauthorized" ? "API key inválida." :
      error && error.kind === "forbidden" ? "Tu usuario no tiene acceso al panel." :
      error && error.message ? error.message : "No se pudo iniciar sesión.";
    setStatus(message, "error");
    input.focus();
  } finally {
    setBusy(submit, false);
  }
}

function startSession() {
  $("view-login").hidden = true;
  $("app").hidden = false;
  $("session").hidden = false;
  $("whoami").textContent = `${state.user.external_id} · ${label(ROLE_LABELS, state.user.role)}`;
  setStatus("");
  const tabs = tabsForRole(state.user.role);
  const list = $("tabs");
  list.replaceChildren();
  for (const tab of tabs) {
    const button = el("button", { text: tab.label, className: "btn", attrs: { type: "button", "data-tab": tab.id } });
    list.append(el("li", {}, [button]));
  }
  if (tabs.length === 0) {
    showView("denied");
    return;
  }
  selectTab(tabs[0].id);
}

function selectTab(id) {
  if (!canView(state.user.role, id)) return;
  state.tab = id;
  for (const button of $("tabs").querySelectorAll("button")) {
    if (button.dataset.tab === id) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  showView(id);
  stopPolling();
  if (id === "approvals") {
    loadApprovals();
    startPolling();
  } else if (id === "conversations") {
    loadConversations();
  } else if (id === "audit") {
    loadAudit();
  }
}

// ---- approvals -----------------------------------------------------------------------------

function renderApproval(item) {
  const params = el("pre", { text: formatParams(item.parameters), attrs: { tabindex: "0", "aria-label": "Parámetros" } });
  const approve = el("button", { text: "Aprobar", className: "btn primary", attrs: { type: "button" } });
  const reject = el("button", { text: "Rechazar", className: "btn danger", attrs: { type: "button" } });
  approve.addEventListener("click", () => openConfirm(item, "approved", approve));
  reject.addEventListener("click", () => openConfirm(item, "rejected", reject));
  const meta = `${label(CHANNEL_LABELS, item.channel)} · ${item.external_conversation_id} · pausada desde ${formatDate(item.created_at)}`;
  return el("li", { className: "card" }, [
    el("h3", { text: item.tool_name }),
    el("p", { text: meta, className: "meta" }),
    params,
    el("div", { className: "actions" }, [approve, reject]),
  ]);
}

async function loadApprovals({ silent = false } = {}) {
  const mine = ++seq.approvals;
  const refresh = $("approvals-refresh");
  setBusy(refresh, true);
  if (!silent) setStatus("Cargando aprobaciones…");
  try {
    const data = await api.get(`/admin/approvals${buildQuery({ limit: 200 })}`);
    if (mine !== seq.approvals) return;
    const list = $("approvals-list");
    list.replaceChildren(...data.items.map(renderApproval));
    $("approvals-empty").hidden = data.items.length > 0;
    setStatus(`${data.items.length} aprobación(es) pendiente(s).`);
  } catch (error) {
    if (mine === seq.approvals) handleError(error);
  } finally {
    if (mine === seq.approvals) setBusy(refresh, false);
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(() => {
    // Paused while the tab is hidden, and while a decision dialog is open.
    if (document.hidden || state.tab !== "approvals" || $("confirm").open) return;
    loadApprovals({ silent: true });
  }, POLL_MS);
}

function stopPolling() {
  if (pollTimer !== null) clearInterval(pollTimer);
  pollTimer = null;
}

function openConfirm(item, decision, trigger) {
  confirmContext = { item, decision, trigger };
  lastFocus = trigger;
  $("confirm-title").textContent = decision === "approved" ? "Aprobar acción" : "Rechazar acción";
  $("confirm-desc").textContent = decisionSummary(decision);
  $("confirm-tool").textContent = item.tool_name;
  $("confirm-params").textContent = formatParams(item.parameters);
  $("confirm-reason").value = "";
  $("confirm-error").textContent = "";
  setBusy($("confirm-ok"), false);
  $("confirm-cancel").disabled = false;
  $("confirm").showModal(); // native modal: focus trap, inert background, Esc closes
  $("confirm-cancel").focus(); // safe default focus: never the deciding button
}

function closeConfirm() {
  const dialog = $("confirm");
  if (dialog.open) dialog.close();
  confirmContext = null;
}

async function onConfirmSubmit(event) {
  event.preventDefault();
  if (!confirmContext || state.pending.has(confirmContext.item.call_id)) return;
  const { item, decision } = confirmContext;
  const ok = $("confirm-ok");
  state.pending.add(item.call_id);
  setBusy(ok, true);
  $("confirm-cancel").disabled = true;
  try {
    const result = await api.post(
      `/admin/approvals/${encodeURIComponent(item.call_id)}/decision`,
      decisionBody(item, decision, $("confirm-reason").value),
    );
    closeConfirm();
    setStatus(`Acción ${label(DECISION_LABELS, decision).toLowerCase()}: ${result.status}.`, "ok");
    await loadApprovals({ silent: true });
    $("approvals-refresh").focus();
  } catch (error) {
    if (error && error.kind === "unauthorized") return;
    $("confirm-error").textContent = error && error.message ? error.message : "No se pudo enviar la decisión.";
    if (error && error.kind === "conflict") {
      loadApprovals({ silent: true });
    }
    $("confirm-cancel").disabled = false;
    setBusy(ok, false);
  } finally {
    state.pending.delete(item.call_id);
  }
}

// ---- conversations -------------------------------------------------------------------------

async function loadConversations() {
  const mine = ++seq.conv;
  const { offset, channel, user } = state.conv;
  setStatus("Cargando conversaciones…");
  try {
    const data = await api.get(`/admin/conversations${buildQuery({ channel, user, limit: PAGE_SIZE, offset })}`);
    if (mine !== seq.conv) return;
    const rows = data.items.map((row) => {
      const open = el("button", { text: "Ver mensajes", className: "btn", attrs: { type: "button" } });
      open.addEventListener("click", () => openMessages(row.thread_id));
      return el("tr", {}, [
        el("td", { text: label(CHANNEL_LABELS, row.channel) }),
        el("td", { text: row.external_conversation_id }),
        el("td", { text: row.status }),
        el("td", { text: formatDate(row.updated_at) }),
        el("td", {}, [open]),
      ]);
    });
    $("conv-body").replaceChildren(...rows);
    $("conv-empty").hidden = rows.length > 0;
    setPager("conv-pager", data.total, data.limit, data.offset);
    setStatus(`${data.total} conversación(es).`);
  } catch (error) {
    if (mine === seq.conv) handleError(error);
  }
}

async function openMessages(threadId) {
  state.messages = { threadId, offset: 0 };
  showView("messages");
  await loadMessages();
  $("msg-title").focus();
}

async function loadMessages() {
  const mine = ++seq.messages;
  const { threadId, offset } = state.messages;
  setStatus("Cargando mensajes…");
  try {
    const data = await api.get(
      `/admin/conversations/${encodeURIComponent(threadId)}/messages${buildQuery({ limit: PAGE_SIZE, offset })}`,
    );
    if (mine !== seq.messages) return;
    $("msg-title").textContent = `Mensajes · ${label(CHANNEL_LABELS, data.channel)} · ${data.external_conversation_id}`;
    const items = data.items.map((m) =>
      el("li", {}, [
        el("p", { text: `${m.role} · ${formatDate(m.created_at)}`, className: "meta" }),
        el("p", { text: m.content, className: "content" }),
      ]),
    );
    $("messages-list").replaceChildren(...items);
    $("messages-empty").hidden = items.length > 0;
    setPager("msg-pager", data.total, data.limit, data.offset);
    setStatus(`${data.total} mensaje(s).`);
  } catch (error) {
    if (mine === seq.messages) handleError(error);
  }
}

// ---- audit ---------------------------------------------------------------------------------

async function loadAudit() {
  const mine = ++seq.audit;
  const { offset, status, tool } = state.audit;
  setStatus("Cargando auditoría…");
  try {
    const data = await api.get(`/admin/audit/tool-executions${buildQuery({ status, tool, limit: PAGE_SIZE, offset })}`);
    if (mine !== seq.audit) return;
    const rows = data.items.map((row) =>
      el("tr", {}, [
        el("td", { text: formatDate(row.created_at) }),
        el("td", { text: row.tool_name }),
        el("td", { text: label(DECISION_LABELS, row.decision) }),
        el("td", { text: label(STATUS_LABELS, row.status) }),
        el("td", { text: row.approved_by ?? "—" }),
        el("td", { text: formatDate(row.completed_at) }),
      ]),
    );
    $("audit-body").replaceChildren(...rows);
    $("audit-empty").hidden = rows.length > 0;
    $("audit-caption").textContent =
      `Ejecuciones de herramientas, de la más reciente a la más antigua (${data.total} en total)`;
    setPager("audit-pager", data.total, data.limit, data.offset);
    setStatus(`${data.total} ejecución(es).`);
  } catch (error) {
    if (mine === seq.audit) handleError(error);
  }
}

// ---- wiring --------------------------------------------------------------------------------

function pagerClick(pagerId, getOffset, setOffset, reload) {
  $(pagerId).addEventListener("click", (event) => {
    const button = event.target.closest("button[data-dir]");
    if (!button || button.disabled) return;
    const current = getOffset();
    setOffset(button.dataset.dir === "next" ? current + PAGE_SIZE : Math.max(0, current - PAGE_SIZE));
    reload();
  });
}

function init() {
  $("login-form").addEventListener("submit", onLogin);
  $("logout").addEventListener("click", () => showLogin(""));
  $("tabs").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-tab]");
    if (button) selectTab(button.dataset.tab);
  });
  $("approvals-refresh").addEventListener("click", () => loadApprovals());
  $("conv-filters").addEventListener("submit", (event) => {
    event.preventDefault();
    state.conv = { offset: 0, channel: $("conv-channel").value, user: $("conv-user").value };
    loadConversations();
  });
  $("audit-filters").addEventListener("submit", (event) => {
    event.preventDefault();
    state.audit = { offset: 0, status: $("audit-status").value, tool: $("audit-tool").value };
    loadAudit();
  });
  pagerClick("conv-pager", () => state.conv.offset, (v) => (state.conv.offset = v), loadConversations);
  pagerClick("msg-pager", () => state.messages.offset, (v) => (state.messages.offset = v), loadMessages);
  pagerClick("audit-pager", () => state.audit.offset, (v) => (state.audit.offset = v), loadAudit);
  $("messages-back").addEventListener("click", () => {
    showView("conversations");
    loadConversations();
  });
  $("confirm-form").addEventListener("submit", onConfirmSubmit);
  $("confirm-cancel").addEventListener("click", closeConfirm);
  // Esc (native cancel) closes without deciding; busy requests keep the dialog open.
  $("confirm").addEventListener("cancel", (event) => {
    if (confirmContext && state.pending.has(confirmContext.item.call_id)) event.preventDefault();
  });
  $("confirm").addEventListener("close", () => {
    confirmContext = null;
    if (lastFocus && lastFocus.isConnected) lastFocus.focus();
    lastFocus = null;
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.tab === "approvals" && state.user) loadApprovals({ silent: true });
  });
  showLogin("");
}

init();
