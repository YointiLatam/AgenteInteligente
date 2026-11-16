import ws from 'k6/ws';
import { Counter, Rate, Trend } from 'k6/metrics';
import { check } from 'k6';

const enabled = __ENV.ENABLE_LOCAL_WS_LOAD === 'true';
const baseUrl = __ENV.CHAT_BASE_URL || 'http://127.0.0.1:8000';
const apiKey = __ENV.CHAT_API_KEY;
const jwt = __ENV.CHAT_JWT;
const users = boundedNumber('WS_VUS', 1, 1, 25);
const durationSeconds = boundedNumber('WS_DURATION_SECONDS', 30, 10, 300);
const responseTimeoutMs = boundedNumber('WS_RESPONSE_TIMEOUT_MS', 5000, 1000, 30000);

const connectionLatency = new Trend('websocket_echo_connection_ms', true);
const firstTokenLatency = new Trend('websocket_echo_first_token_ms', true);
const responseLatency = new Trend('websocket_echo_response_complete_ms', true);
const completed = new Counter('websocket_echo_completed_total');
const attempts = new Counter('websocket_echo_attempts_total');
const errors = new Rate('websocket_echo_errors');

export const options = {
  vus: users,
  duration: `${durationSeconds}s`,
  thresholds: {
    websocket_echo_response_complete_ms: ['p(95)<2500'],
    websocket_echo_errors: ['rate<0.01'],
  },
};

if (!enabled) {
  throw new Error('La carga está deshabilitada. Define ENABLE_LOCAL_WS_LOAD=true para ejecutarla.');
}
if (!isLoopbackHttpUrl(baseUrl)) {
  throw new Error('CHAT_BASE_URL debe ser una URL HTTP de loopback sin ruta ni credenciales.');
}
if ((apiKey && jwt) || (!apiKey && !jwt)) {
  throw new Error('Define exactamente una credencial: CHAT_API_KEY o CHAT_JWT.');
}

function boundedNumber(name, defaultValue, minimum, maximum) {
  const raw = __ENV[name];
  if (raw === undefined || raw === '') return defaultValue;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${name} debe ser un entero entre ${minimum} y ${maximum}.`);
  }
  return value;
}

function isLoopbackHttpUrl(url) {
  if (typeof url !== 'string' || url !== url.trim()) return false;
  const match = /^(https?):\/\/(localhost|127\.0\.0\.1|\[::1\])(?::(\d{1,5}))?\/?$/i.exec(url);
  if (match === null) return false;
  return match[3] === undefined || Number(match[3]) <= 65535;
}

function websocketUrl(url) {
  return `${url.replace(/^http/i, 'ws').replace(/\/$/, '')}/chat/ws`;
}

export default function () {
  const connectionStartedAt = Date.now();
  const headers = apiKey ? { 'X-API-Key': apiKey } : {};
  const url = jwt ? `${websocketUrl(baseUrl)}?token=${encodeURIComponent(jwt)}` : websocketUrl(baseUrl);
  let promptSentAt = null;
  let firstTokenAt = null;
  let opened = false;
  let terminalOutcome = null;

  function finish(outcome) {
    if (terminalOutcome !== null) return false;
    terminalOutcome = outcome;
    attempts.add(1, { outcome });
    errors.add(outcome === 'success' ? 0 : 1);
    if (outcome === 'success') completed.add(1);
    return true;
  }

  const response = ws.connect(url, { headers }, (socket) => {
    socket.on('open', () => {
      opened = true;
      connectionLatency.add(Date.now() - connectionStartedAt);
      promptSentAt = Date.now();
      try {
        socket.send(JSON.stringify({ prompt: 'Consulta local de transporte.' }));
      } catch (_) {
        finish('protocol_error');
        socket.close();
        return;
      }
      socket.setTimeout(() => {
        if (finish('timeout')) socket.close();
      }, responseTimeoutMs);
    });
    socket.on('message', (message) => {
      if (terminalOutcome !== null) return;
      let frame;
      try {
        frame = JSON.parse(message);
      } catch (_) {
        if (finish('protocol_error')) socket.close();
        return;
      }
      if (frame === null || typeof frame !== 'object' || typeof frame.type !== 'string') {
        if (finish('protocol_error')) socket.close();
        return;
      }
      if (frame.type === 'token' && firstTokenAt === null && promptSentAt !== null) {
        firstTokenAt = Date.now();
        firstTokenLatency.add(firstTokenAt - promptSentAt);
        return;
      }
      if (frame.type === 'done' && promptSentAt !== null) {
        if (finish('success')) {
          responseLatency.add(Date.now() - promptSentAt);
          socket.close();
        }
        return;
      }
      if (frame.type === 'error') {
        if (finish('protocol_error')) socket.close();
        return;
      }
      if (finish('protocol_error')) socket.close();
    });
    socket.on('error', () => {
      finish(opened ? 'premature_close' : 'connection_error');
    });
    socket.on('close', () => {
      if (terminalOutcome === null) finish(opened ? 'premature_close' : 'connection_error');
    });
  });

  check(response, { 'websocket upgrade successful': (result) => result && result.status === 101 });
  if (!response || response.status !== 101) finish('connection_error');
}
