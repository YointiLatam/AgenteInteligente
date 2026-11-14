// Human-in-the-loop approval: pure validation helpers plus a DOM builder that
// only ever writes untrusted values through textContent.

export const DECISIONS = ['approved', 'rejected'];

/** Validates an `approval_required` frame. Returns { ok, request } or { ok:false, error }. */
export function validateApprovalRequired(frame) {
  if (!frame || typeof frame !== 'object' || frame.type !== 'approval_required') {
    return { ok: false, error: 'not_approval_required' };
  }
  if (typeof frame.thread_id !== 'string' || frame.thread_id === '') return { ok: false, error: 'invalid_thread_id' };
  if (typeof frame.turn_id !== 'string' || frame.turn_id === '') return { ok: false, error: 'invalid_turn_id' };
  if (typeof frame.call_id !== 'string' || frame.call_id === '') return { ok: false, error: 'invalid_call_id' };
  if (typeof frame.tool_name !== 'string' || frame.tool_name === '') return { ok: false, error: 'invalid_tool_name' };
  const params = frame.parameters ?? {};
  if (params === null || typeof params !== 'object' || Array.isArray(params)) {
    return { ok: false, error: 'invalid_parameters' };
  }
  if (frame.status !== undefined && frame.status !== 'pending_approval') {
    return { ok: false, error: 'invalid_status' };
  }
  return {
    ok: true,
    request: {
      threadId: frame.thread_id,
      turnId: frame.turn_id,
      callId: frame.call_id,
      toolName: frame.tool_name,
      parameters: params,
    },
  };
}

/**
 * Builds the client message that echoes the identifiers of the `approval_required` frame.
 * The backend derives the approver from the credentials, never from this payload.
 */
export function buildApprovalPayload(request, decision, reason) {
  if (!DECISIONS.includes(decision)) throw new Error(`Invalid decision: ${decision}`);
  const payload = {
    type: 'approval',
    thread_id: request.threadId,
    turn_id: request.turnId,
    call_id: request.callId,
    tool_name: request.toolName,
    decision,
  };
  if (typeof reason === 'string' && reason !== '') payload.reason = reason;
  return payload;
}

/** Validates an `approval_result` frame; returns { callId, status, message } or null. */
export function validateApprovalResult(frame) {
  if (!frame || frame.type !== 'approval_result') return null;
  if (typeof frame.call_id !== 'string' || !['executed', 'rejected'].includes(frame.status)) return null;
  return {
    callId: frame.call_id,
    status: frame.status,
    message: typeof frame.message === 'string' ? frame.message : '',
  };
}

export function resultText({ status, message }) {
  const base = status === 'executed' ? 'Acción aprobada y ejecutada.' : 'Acción rechazada.';
  return message ? `${base} ${message}` : base;
}

/** Plain-text rendering of a parameter value (never HTML). */
export function formatParameter(value) {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}

/** Next focus index for a Tab focus trap over `count` focusable elements. */
export function nextFocusIndex(current, count, backwards) {
  if (count <= 0) return -1;
  if (current < 0) return backwards ? count - 1 : 0;
  return (current + (backwards ? -1 : 1) + count) % count;
}

/**
 * Builds the modal subtree. Returns { element, focusInitial, destroy }.
 * Esc only moves focus to "Rechazar"; it never decides on its own.
 */
export function createApprovalModal(doc, request, onDecision) {
  const overlay = doc.createElement('div');
  overlay.className = 'approval-overlay';

  const dialog = doc.createElement('div');
  dialog.className = 'approval-dialog';
  dialog.setAttribute('role', 'alertdialog');
  dialog.setAttribute('aria-modal', 'true');
  dialog.setAttribute('aria-labelledby', 'approval-title');
  dialog.setAttribute('aria-describedby', 'approval-desc');

  const title = doc.createElement('h2');
  title.id = 'approval-title';
  title.textContent = 'Aprobación requerida';

  const desc = doc.createElement('p');
  desc.id = 'approval-desc';
  desc.textContent = `El agente quiere ejecutar la acción "${request.toolName}". Revisa los parámetros antes de decidir.`;

  const list = doc.createElement('dl');
  list.className = 'approval-params';
  for (const [key, value] of Object.entries(request.parameters)) {
    const dt = doc.createElement('dt');
    dt.textContent = key;
    const dd = doc.createElement('dd');
    dd.textContent = formatParameter(value);
    list.append(dt, dd);
  }

  const actions = doc.createElement('div');
  actions.className = 'approval-actions';
  const reject = doc.createElement('button');
  reject.type = 'button';
  reject.className = 'btn-secondary';
  reject.textContent = 'Rechazar';
  const approve = doc.createElement('button');
  approve.type = 'button';
  approve.className = 'btn-primary';
  approve.textContent = 'Aprobar';
  actions.append(reject, approve);

  const buttons = [reject, approve];
  let decided = false;
  const decide = (decision) => {
    if (decided) return;
    decided = true;
    buttons.forEach((b) => (b.disabled = true));
    onDecision(buildApprovalPayload(request, decision));
  };
  approve.addEventListener('click', () => decide('approved'));
  reject.addEventListener('click', () => decide('rejected'));

  const onKeydown = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      reject.focus();
    } else if (event.key === 'Tab') {
      const root = dialog.getRootNode();
      const active = root.activeElement ?? doc.activeElement;
      const next = nextFocusIndex(buttons.indexOf(active), buttons.length, event.shiftKey);
      event.preventDefault();
      buttons[next].focus();
    }
  };
  overlay.addEventListener('keydown', onKeydown);

  dialog.append(title, desc, list, actions);
  overlay.append(dialog);
  return {
    element: overlay,
    focusInitial: () => reject.focus(),
    destroy: () => overlay.remove(),
  };
}
