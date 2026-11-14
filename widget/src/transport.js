// Transport layer: WebSocket with exponential backoff + jitter, falling back
// to SSE (POST /chat/stream). Frame parsing helpers are pure and exported.

export const DEFAULT_BACKOFF = { base: 500, max: 15000, jitter: 0.5 };

/**
 * Delay in ms before reconnect attempt `attempt` (0-based).
 * Result lies in [exp * (1 - jitter), exp] where exp = min(max, base * 2^attempt).
 */
export function backoffDelay(attempt, options = {}, random = Math.random) {
  const { base, max, jitter } = { ...DEFAULT_BACKOFF, ...options };
  const exp = Math.min(max, base * 2 ** Math.max(0, attempt));
  return Math.round(exp * (1 - jitter) + random() * exp * jitter);
}

/** Incremental SSE parser. push() accepts arbitrary chunk boundaries. */
export function createSseParser(onFrame) {
  let buffer = '';
  const dispatch = (block) => {
    let event = 'message';
    const data = [];
    for (const line of block.split('\n')) {
      if (line === '' || line.startsWith(':')) continue;
      const idx = line.indexOf(':');
      const field = idx === -1 ? line : line.slice(0, idx);
      let value = idx === -1 ? '' : line.slice(idx + 1);
      if (value.startsWith(' ')) value = value.slice(1);
      if (field === 'event') event = value;
      else if (field === 'data') data.push(value);
    }
    if (data.length) onFrame({ event, data: data.join('\n') });
  };
  return {
    push(chunk) {
      buffer += chunk.replace(/\r\n?/g, '\n');
      let sep;
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        dispatch(buffer.slice(0, sep));
        buffer = buffer.slice(sep + 2);
      }
    },
    flush() {
      if (buffer.trim()) dispatch(buffer);
      buffer = '';
    },
  };
}

/** Converts an SSE frame into the same message shape used by WebSocket frames. */
export function sseFrameToMessage(frame) {
  let payload = {};
  try {
    payload = JSON.parse(frame.data);
  } catch {
    return { type: 'error', message: 'Respuesta del servidor no válida.' };
  }
  if (payload === null || typeof payload !== 'object') {
    return { type: 'error', message: 'Respuesta del servidor no válida.' };
  }
  return { ...payload, type: payload.type ?? frame.event };
}

export function toWsUrl(apiUrl, path, token) {
  const url = new URL(path, apiUrl.endsWith('/') ? apiUrl : `${apiUrl}/`);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  if (token) url.searchParams.set('token', token);
  return url.toString();
}

function httpUrl(apiUrl, path) {
  return new URL(path.replace(/^\//, ''), apiUrl.endsWith('/') ? apiUrl : `${apiUrl}/`).toString();
}

/** User-facing message for a failed `POST /chat/approval`, by HTTP status. */
export function approvalErrorMessage(status) {
  if (status === 409) return 'La acción ya no está pendiente de aprobación.';
  if (status === 404) return 'No se encontró la acción pendiente.';
  if (status === 401) return 'Tu sesión no es válida. Recarga la página.';
  if (status === 422) return 'La decisión no es válida.';
  if (status === 503) return 'El servicio de aprobación no está disponible ahora.';
  return 'No se pudo enviar la decisión. Intenta de nuevo.';
}

export class ChatTransport {
  constructor({ apiUrl, apiKey, onMessage, onStatus, maxWsFailures = 3, WebSocketImpl, fetchImpl, backoff } = {}) {
    this.apiUrl = apiUrl;
    this.apiKey = apiKey;
    this.onMessage = onMessage ?? (() => {});
    this.onStatus = onStatus ?? (() => {});
    this.maxWsFailures = maxWsFailures;
    this.WebSocketImpl = WebSocketImpl ?? globalThis.WebSocket;
    this.fetchImpl = fetchImpl ?? ((...a) => globalThis.fetch(...a));
    this.backoff = backoff;
    this.mode = 'ws';
    this.ws = null;
    this.failures = 0;
    this.timer = null;
    this.closed = false;
    this.abort = null;
  }

  start() {
    this.closed = false;
    if (!this.WebSocketImpl) return this.useSse();
    return this.connectWs();
  }

  stop() {
    this.closed = true;
    clearTimeout(this.timer);
    this.abort?.abort();
    this.ws?.close();
    this.ws = null;
  }

  useSse() {
    this.mode = 'sse';
    this.onStatus('connected');
  }

  async exchangeToken() {
    if (!this.apiKey) return null;
    const res = await this.fetchImpl(httpUrl(this.apiUrl, '/auth/token'), {
      method: 'POST',
      headers: { 'X-API-Key': this.apiKey },
    });
    if (!res.ok) throw new Error(`auth ${res.status}`);
    return (await res.json()).access_token ?? null;
  }

  async connectWs() {
    if (this.closed) return;
    this.onStatus(this.failures > 0 ? 'reconnecting' : 'connecting');
    let token = null;
    try {
      token = await this.exchangeToken();
    } catch {
      return this.handleWsFailure();
    }
    if (this.closed) return;
    let opened = false;
    let ws;
    try {
      ws = new this.WebSocketImpl(toWsUrl(this.apiUrl, '/chat/ws', token));
    } catch {
      return this.handleWsFailure();
    }
    this.ws = ws;
    ws.onopen = () => {
      opened = true;
      this.failures = 0;
      this.onStatus('connected');
    };
    ws.onmessage = (event) => {
      try {
        this.onMessage(JSON.parse(event.data));
      } catch {
        this.onMessage({ type: 'error', message: 'Respuesta del servidor no válida.' });
      }
    };
    ws.onclose = () => {
      if (this.closed || this.ws !== ws) return;
      this.ws = null;
      if (opened) this.failures = 0;
      this.handleWsFailure();
    };
  }

  handleWsFailure() {
    if (this.closed) return;
    this.failures += 1;
    if (this.failures >= this.maxWsFailures) return this.useSse();
    this.onStatus('reconnecting');
    this.timer = setTimeout(() => this.connectWs(), backoffDelay(this.failures - 1, this.backoff));
  }

  send(prompt) {
    if (this.mode === 'sse') return this.streamSse(prompt);
    if (this.ws?.readyState === 1) {
      this.ws.send(JSON.stringify({ prompt }));
    } else {
      this.onMessage({ type: 'error', message: 'Sin conexión con el agente. Intenta de nuevo en unos segundos.' });
    }
    return Promise.resolve();
  }

  /** Sends an approval decision: over WS, or POST /chat/approval in SSE mode. */
  async sendApproval(payload) {
    if (this.mode === 'ws' && this.ws?.readyState === 1) {
      this.ws.send(JSON.stringify(payload));
      return;
    }
    try {
      const res = await this.fetchImpl(httpUrl(this.apiUrl, '/chat/approval'), {
        method: 'POST',
        headers: this.headers(),
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        this.onMessage({ type: 'error', message: approvalErrorMessage(res.status) });
        return;
      }
      this.onMessage({ type: 'approval_result', ...(await res.json()) });
    } catch {
      this.onMessage({ type: 'error', message: 'No se pudo enviar la decisión. Intenta de nuevo.' });
    }
  }

  headers() {
    const headers = { 'Content-Type': 'application/json' };
    if (this.apiKey) headers['X-API-Key'] = this.apiKey;
    return headers;
  }

  async streamSse(prompt) {
    this.abort = new AbortController();
    const parser = createSseParser((frame) => this.onMessage(sseFrameToMessage(frame)));
    try {
      const res = await this.fetchImpl(httpUrl(this.apiUrl, '/chat/stream'), {
        method: 'POST',
        headers: this.headers(),
        body: JSON.stringify({ prompt }),
        signal: this.abort.signal,
      });
      if (!res.ok || !res.body) throw new Error(`stream ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        parser.push(decoder.decode(value, { stream: true }));
      }
      parser.flush();
    } catch (err) {
      if (err?.name !== 'AbortError') {
        this.onMessage({ type: 'error', message: 'Se interrumpió la conexión con el agente.' });
      }
    }
  }
}
