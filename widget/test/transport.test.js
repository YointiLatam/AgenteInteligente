import { test } from 'node:test';
import assert from 'node:assert/strict';
import { approvalErrorMessage, backoffDelay, ChatTransport, createSseParser, sseFrameToMessage, toWsUrl } from '../src/transport.js';

function collect() {
  const frames = [];
  return { frames, parser: createSseParser((f) => frames.push(f)) };
}

test('SSE parser handles a complete frame', () => {
  const { frames, parser } = collect();
  parser.push('event: token\ndata: {"text":"hi"}\n\n');
  assert.deepEqual(frames, [{ event: 'token', data: '{"text":"hi"}' }]);
});

test('SSE parser reassembles frames split across chunks', () => {
  const { frames, parser } = collect();
  const raw = 'event: token\ndata: {"text":"a"}\n\nevent: done\ndata: {}\n\n';
  for (const ch of raw) parser.push(ch);
  assert.equal(frames.length, 2);
  assert.equal(frames[1].event, 'done');
});

test('SSE parser supports CRLF, comments and multi-line data', () => {
  const { frames, parser } = collect();
  parser.push(': ping\r\nevent: x\r\ndata: a\r\ndata: b\r\n\r\n');
  assert.deepEqual(frames, [{ event: 'x', data: 'a\nb' }]);
});

test('SSE parser flush emits a trailing unterminated frame', () => {
  const { frames, parser } = collect();
  parser.push('event: done\ndata: {}');
  assert.equal(frames.length, 0);
  parser.flush();
  assert.equal(frames.length, 1);
});

test('sseFrameToMessage maps event name and tolerates bad JSON', () => {
  assert.deepEqual(sseFrameToMessage({ event: 'token', data: '{"text":"a"}' }), { text: 'a', type: 'token' });
  assert.equal(sseFrameToMessage({ event: 'token', data: 'nope' }).type, 'error');
  assert.equal(sseFrameToMessage({ event: 'token', data: '5' }).type, 'error');
});

test('backoff stays within [exp*(1-jitter), exp] and caps at max', () => {
  const opts = { base: 500, max: 15000, jitter: 0.5 };
  for (let attempt = 0; attempt < 12; attempt++) {
    const exp = Math.min(15000, 500 * 2 ** attempt);
    assert.equal(backoffDelay(attempt, opts, () => 0), Math.round(exp * 0.5));
    assert.equal(backoffDelay(attempt, opts, () => 1), exp);
    const mid = backoffDelay(attempt, opts, () => 0.5);
    assert.ok(mid >= exp * 0.5 && mid <= exp);
  }
});

test('backoff grows exponentially before the cap', () => {
  const d = (n) => backoffDelay(n, { jitter: 0 });
  assert.ok(d(1) === 2 * d(0) && d(2) === 2 * d(1));
});

test('toWsUrl switches scheme and appends the token', () => {
  assert.equal(toWsUrl('https://api.x.com', '/chat/ws', 'T'), 'wss://api.x.com/chat/ws?token=T');
  assert.equal(toWsUrl('http://localhost:8000/', '/chat/ws'), 'ws://localhost:8000/chat/ws');
});

const approval = { type: 'approval', thread_id: 't1', turn_id: 'u1', call_id: 'c1', tool_name: 'x', decision: 'approved' };

test('sendApproval posts the full payload in SSE mode and relays the result', async () => {
  const messages = [];
  const calls = [];
  const transport = new ChatTransport({
    apiUrl: 'https://api.x.com',
    apiKey: 'K',
    onMessage: (m) => messages.push(m),
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      return { ok: true, status: 200, json: async () => ({ call_id: 'c1', status: 'executed' }) };
    },
  });
  transport.mode = 'sse';
  await transport.sendApproval(approval);
  assert.equal(calls[0].url, 'https://api.x.com/chat/approval');
  assert.deepEqual(JSON.parse(calls[0].init.body), approval);
  assert.equal(calls[0].init.headers['X-API-Key'], 'K');
  assert.deepEqual(messages, [{ type: 'approval_result', call_id: 'c1', status: 'executed' }]);
});

test('sendApproval sends the payload over an open WebSocket', async () => {
  const sent = [];
  const transport = new ChatTransport({ apiUrl: 'https://api.x.com' });
  transport.ws = { readyState: 1, send: (d) => sent.push(d) };
  await transport.sendApproval(approval);
  assert.deepEqual(JSON.parse(sent[0]), approval);
});

test('sendApproval reports a 409 with a specific message', async () => {
  const messages = [];
  const transport = new ChatTransport({
    apiUrl: 'https://api.x.com',
    onMessage: (m) => messages.push(m),
    fetchImpl: async () => ({ ok: false, status: 409, json: async () => ({}) }),
  });
  transport.mode = 'sse';
  await transport.sendApproval(approval);
  assert.deepEqual(messages, [{ type: 'error', message: approvalErrorMessage(409) }]);
  assert.notEqual(approvalErrorMessage(409), approvalErrorMessage(500));
});

test('a 403 explains the missing permission instead of the generic failure', () => {
  assert.match(approvalErrorMessage(403), /permiso/);
  assert.notEqual(approvalErrorMessage(403), approvalErrorMessage(500));
});
