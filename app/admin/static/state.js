// Minimal in-memory UI state (no persistence by design).
export function createState() {
  return {
    user: null,
    tab: null,
    conv: { offset: 0, channel: "", user: "" },
    messages: { threadId: null, offset: 0 },
    audit: { offset: 0, status: "", tool: "" },
    pending: new Set(),
  };
}

export const PAGE_SIZE = 20;
export const POLL_MS = 30000;
