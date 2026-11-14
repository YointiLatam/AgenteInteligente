import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildApprovalPayload,
  formatParameter,
  nextFocusIndex,
  resultText,
  validateApprovalRequired,
  validateApprovalResult,
} from '../src/approval.js';

const frame = {
  type: 'approval_required',
  thread_id: 't1',
  turn_id: 'u1',
  call_id: 'c1',
  tool_name: 'send_whatsapp',
  parameters: { to: '+34600', body: '<b>hola</b>' },
  status: 'pending_approval',
};

test('accepts a valid approval_required frame', () => {
  const r = validateApprovalRequired(frame);
  assert.equal(r.ok, true);
  assert.deepEqual(r.request, {
    threadId: 't1',
    turnId: 'u1',
    callId: 'c1',
    toolName: 'send_whatsapp',
    parameters: frame.parameters,
  });
});

test('rejects malformed approval_required frames', () => {
  assert.equal(validateApprovalRequired(null).ok, false);
  assert.equal(validateApprovalRequired({ ...frame, type: 'token' }).ok, false);
  assert.equal(validateApprovalRequired({ ...frame, thread_id: '' }).error, 'invalid_thread_id');
  assert.equal(validateApprovalRequired({ ...frame, turn_id: undefined }).error, 'invalid_turn_id');
  assert.equal(validateApprovalRequired({ ...frame, call_id: '' }).error, 'invalid_call_id');
  assert.equal(validateApprovalRequired({ ...frame, tool_name: 5 }).error, 'invalid_tool_name');
  assert.equal(validateApprovalRequired({ ...frame, parameters: [] }).error, 'invalid_parameters');
  assert.equal(validateApprovalRequired({ ...frame, parameters: 'x' }).error, 'invalid_parameters');
  assert.equal(validateApprovalRequired({ ...frame, status: 'executed' }).error, 'invalid_status');
});

test('missing parameters default to an empty object', () => {
  const rest = { ...frame };
  delete rest.parameters;
  assert.deepEqual(validateApprovalRequired(rest).request.parameters, {});
});

test('buildApprovalPayload echoes the identifiers and only allows approved or rejected', () => {
  const { request } = validateApprovalRequired(frame);
  assert.deepEqual(buildApprovalPayload(request, 'approved'), {
    type: 'approval',
    thread_id: 't1',
    turn_id: 'u1',
    call_id: 'c1',
    tool_name: 'send_whatsapp',
    decision: 'approved',
  });
  assert.equal(buildApprovalPayload(request, 'rejected').decision, 'rejected');
  assert.equal(buildApprovalPayload(request, 'rejected', 'no gracias').reason, 'no gracias');
  assert.equal('reason' in buildApprovalPayload(request, 'approved'), false);
  assert.throws(() => buildApprovalPayload(request, 'maybe'));
});

test('validateApprovalResult validates status and message', () => {
  assert.deepEqual(validateApprovalResult({ type: 'approval_result', call_id: 'c1', status: 'executed' }), {
    callId: 'c1', status: 'executed', message: '',
  });
  assert.equal(validateApprovalResult({ type: 'approval_result', call_id: 'c1', status: 'pending' }), null);
  assert.equal(validateApprovalResult({ type: 'token' }), null);
  assert.match(resultText({ status: 'rejected', message: 'Cancelada' }), /rechazada\. Cancelada/i);
});

test('formatParameter returns plain text for strings and JSON for objects', () => {
  assert.equal(formatParameter('<b>x</b>'), '<b>x</b>');
  assert.equal(formatParameter({ a: 1 }), '{\n  "a": 1\n}');
  assert.equal(formatParameter(undefined), 'undefined');
});

test('nextFocusIndex wraps in both directions', () => {
  assert.equal(nextFocusIndex(1, 2, false), 0);
  assert.equal(nextFocusIndex(0, 2, true), 1);
  assert.equal(nextFocusIndex(-1, 2, false), 0);
  assert.equal(nextFocusIndex(-1, 2, true), 1);
  assert.equal(nextFocusIndex(0, 0, false), -1);
});
