import test from "node:test";
import assert from "node:assert/strict";
import {
  buildQuery, canView, decisionBody, decisionSummary, errorInfo, formatDate, formatParams, label,
  pageInfo, pageLabel, tabsForRole, CHANNEL_LABELS,
} from "../../app/admin/static/render.js";
import { createApi, ApiError } from "../../app/admin/static/api.js";

test("tabsForRole hides audit from operators", () => {
  assert.deepEqual(tabsForRole("operator").map((t) => t.id), ["approvals", "conversations"]);
  assert.deepEqual(tabsForRole("admin").map((t) => t.id), ["approvals", "conversations", "audit"]);
  assert.deepEqual(tabsForRole("end_user"), []);
  assert.deepEqual(tabsForRole(undefined), []);
});

test("canView follows the role matrix", () => {
  assert.equal(canView("operator", "audit"), false);
  assert.equal(canView("admin", "audit"), true);
  assert.equal(canView("operator", "approvals"), true);
});

test("pageInfo math", () => {
  assert.deepEqual(pageInfo(0, 20, 0), { page: 1, pages: 1, hasPrev: false, hasNext: false, prevOffset: 0, nextOffset: 20 });
  const mid = pageInfo(45, 20, 20);
  assert.equal(mid.page, 2);
  assert.equal(mid.pages, 3);
  assert.equal(mid.hasPrev, true);
  assert.equal(mid.hasNext, true);
  assert.equal(pageInfo(40, 20, 20).hasNext, false);
  assert.equal(pageInfo(45, 20, 0).prevOffset, 0);
  assert.equal(pageLabel(45, 20, 40), "Página 3 de 3 (45 en total)");
});

test("buildQuery drops empty values and encodes", () => {
  assert.equal(buildQuery({ a: "", b: undefined, c: null }), "");
  assert.equal(buildQuery({ user: " a b&c ", limit: 20, offset: 0 }), "?user=a+b%26c&limit=20&offset=0");
});

test("errorInfo maps statuses distinctly", () => {
  assert.equal(errorInfo(401).kind, "unauthorized");
  assert.equal(errorInfo(403).kind, "forbidden");
  assert.match(errorInfo(403).message, /Sin permiso/);
  assert.equal(errorInfo(409).kind, "conflict");
  assert.equal(errorInfo(503).kind, "unavailable");
  assert.equal(errorInfo(0).kind, "unknown");
});

test("formatParams returns text, never markup, and survives bad input", () => {
  assert.equal(formatParams({ a: "<img onerror=x>" }), '{\n  "a": "<img onerror=x>"\n}');
  assert.equal(formatParams(undefined), "{}");
  const cyclic = {};
  cyclic.self = cyclic;
  assert.equal(formatParams(cyclic), "[parámetros no legibles]");
});

test("formatDate and label fall back safely", () => {
  assert.equal(formatDate(null), "—");
  assert.equal(formatDate("not a date"), "—");
  assert.notEqual(formatDate("2026-11-14T10:00:00+00:00"), "—");
  assert.equal(label(CHANNEL_LABELS, "whatsapp"), "WhatsApp");
  assert.equal(label(CHANNEL_LABELS, "__proto__"), "__proto__");
  assert.equal(label(CHANNEL_LABELS, undefined), "—");
});

test("decisionBody only sends the allowed fields and optional trimmed reason", () => {
  const item = { thread_id: "t", turn_id: "u", tool_name: "x", call_id: "c", approved_by: "evil" };
  assert.deepEqual(decisionBody(item, "approved", "  "), { thread_id: "t", turn_id: "u", tool_name: "x", decision: "approved" });
  assert.equal(decisionBody(item, "rejected", " no ").reason, "no");
  assert.match(decisionSummary("approved"), /aprobar/);
  assert.match(decisionSummary("rejected"), /rechazar/);
});

function fakeFetch(responses, calls = []) {
  return async (url, init) => {
    calls.push({ url, init });
    const { status, body } = responses.shift();
    return { ok: status >= 200 && status < 300, status, json: async () => body };
  };
}

test("api keeps the JWT in memory and sends it as Bearer", async () => {
  const calls = [];
  const api = createApi({
    fetchImpl: fakeFetch([{ status: 200, body: { access_token: "jwt" } }, { status: 200, body: { ok: 1 } }], calls),
  });
  await api.login("secret-key");
  assert.equal(calls[0].init.headers["X-API-Key"], "secret-key");
  assert.equal(calls[0].init.headers.Authorization, undefined);
  await api.get("/admin/me");
  assert.equal(calls[1].init.headers.Authorization, "Bearer jwt");
  assert.equal(calls[1].init.credentials, "omit");
  api.logout();
  assert.equal(api.hasSession(), false);
});

test("api 401 clears the session and notifies; 403 does not", async () => {
  let notified = 0;
  const api = createApi({
    onUnauthorized: () => notified++,
    fetchImpl: fakeFetch([
      { status: 200, body: { access_token: "jwt" } },
      { status: 403, body: { detail: "no" } },
      { status: 401, body: { detail: "exp" } },
    ]),
  });
  await api.login("k");
  await assert.rejects(api.get("/admin/audit/tool-executions"), (e) => e instanceof ApiError && e.kind === "forbidden");
  assert.equal(api.hasSession(), true);
  assert.equal(notified, 0);
  await assert.rejects(api.get("/admin/me"), (e) => e.kind === "unauthorized");
  assert.equal(api.hasSession(), false);
  assert.equal(notified, 1);
});

test("api login failure (401) does not trigger the re-login callback", async () => {
  let notified = 0;
  const api = createApi({ onUnauthorized: () => notified++, fetchImpl: fakeFetch([{ status: 401, body: {} }]) });
  await assert.rejects(api.login("bad"), (e) => e.kind === "unauthorized");
  assert.equal(notified, 0);
});

test("api maps network failures to an ApiError", async () => {
  const api = createApi({ fetchImpl: async () => { throw new TypeError("net"); } });
  await assert.rejects(api.get("/x"), (e) => e instanceof ApiError && e.status === 0);
});
