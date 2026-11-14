import { renderMarkdown } from './markdown.js';
import { ChatTransport } from './transport.js';
import { STYLES } from './styles.js';
import {
  createApprovalModal,
  resultText,
  validateApprovalRequired,
  validateApprovalResult,
} from './approval.js';

const COLOR = /^(#[0-9a-f]{3,8}|[a-z]+|rgba?\([\d\s,.%]+\)|hsla?\([\d\s,.%deg]+\))$/i;
const TEMPLATE = `
<button class="launcher" type="button" aria-label="Abrir chat" aria-expanded="false">&#128172;</button>
<section class="panel" role="dialog" aria-label="Chat" hidden>
  <header><h1></h1><button class="close" type="button" aria-label="Cerrar chat">&times;</button></header>
  <div class="banner error" role="alert" hidden></div>
  <div class="banner reconnecting" role="status" hidden>Reconectando...</div>
  <div class="log" role="log" aria-live="polite" aria-relevant="additions text"></div>
  <div class="typing msg" hidden aria-label="El agente está escribiendo"><span></span><span></span><span></span></div>
  <form>
    <textarea rows="1" placeholder="Escribe tu mensaje..." aria-label="Mensaje"></textarea>
    <button class="send" type="submit">Enviar</button>
  </form>
</section>`;

class AiChatAgent extends HTMLElement {
  static get observedAttributes() {
    return ['title', 'color', 'position'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.current = null; // { el, text } while streaming
    this.modal = null;
    this.busy = false;
  }

  connectedCallback() {
    if (this.shadowRoot.childElementCount) return;
    const style = document.createElement('style');
    style.textContent = STYLES;
    const wrap = document.createElement('div');
    wrap.innerHTML = TEMPLATE; // static template, no user input
    this.shadowRoot.append(style, ...wrap.childNodes);
    const $ = (s) => this.shadowRoot.querySelector(s);
    this.ui = {
      launcher: $('.launcher'), panel: $('.panel'), title: $('h1'), close: $('.close'),
      error: $('.banner.error'), reconnecting: $('.banner.reconnecting'), log: $('.log'),
      typing: $('.typing'), form: $('form'), input: $('textarea'), send: $('.send'),
    };
    this.applyAttributes();
    this.ui.launcher.addEventListener('click', () => this.toggle());
    this.ui.close.addEventListener('click', () => this.toggle(false));
    this.ui.form.addEventListener('submit', (e) => {
      e.preventDefault();
      this.submit();
    });
    this.ui.input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.submit();
      }
    });
    this.shadowRoot.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !this.modal) this.toggle(false);
    });
    this.startTransport();
  }

  disconnectedCallback() {
    this.transport?.stop();
    this.transport = null;
  }

  attributeChangedCallback() {
    if (this.ui) this.applyAttributes();
  }

  applyAttributes() {
    this.ui.title.textContent = this.getAttribute('title') || 'Asistente';
    const color = this.getAttribute('color');
    if (color && COLOR.test(color)) this.style.setProperty('--accent', color);
    else this.style.removeProperty('--accent');
    if (this.getAttribute('position') !== 'bottom-left') this.setAttribute('position', 'bottom-right');
  }

  startTransport() {
    const apiUrl = this.getAttribute('api-url');
    if (!apiUrl) {
      this.showError('Falta el atributo api-url.');
      return;
    }
    this.transport = new ChatTransport({
      apiUrl,
      apiKey: this.getAttribute('api-key') || undefined,
      onMessage: (frame) => this.handleFrame(frame),
      onStatus: (status) => {
        this.ui.reconnecting.hidden = status !== 'reconnecting';
      },
    });
    this.transport.start();
  }

  toggle(force) {
    const open = force ?? this.ui.panel.hidden;
    this.ui.panel.hidden = !open;
    this.ui.launcher.setAttribute('aria-expanded', String(open));
    if (open) this.ui.input.focus();
    else this.ui.launcher.focus();
  }

  submit() {
    const text = this.ui.input.value.trim();
    if (!text || this.busy || !this.transport) return;
    this.ui.input.value = '';
    this.hideError();
    this.addMessage('user', text);
    this.setBusy(true);
    this.transport.send(text);
  }

  setBusy(busy) {
    this.busy = busy;
    this.ui.send.disabled = busy;
    this.ui.typing.hidden = !busy || Boolean(this.current);
  }

  handleFrame(frame) {
    switch (frame?.type) {
      case 'token':
        this.appendToken(String(frame.text ?? ''));
        break;
      case 'done':
        this.current = null;
        this.setBusy(false);
        break;
      case 'error':
        this.current = null;
        this.setBusy(false);
        this.showError(String(frame.message ?? 'Ocurrió un error.'));
        break;
      case 'approval_required':
        this.showApproval(frame);
        break;
      case 'approval_result': {
        const result = validateApprovalResult(frame);
        if (result) this.addMessage('system', resultText(result));
        break;
      }
      default:
        break;
    }
  }

  appendToken(text) {
    if (!this.current) {
      this.current = { el: this.addMessage('agent', ''), text: '' };
      this.ui.typing.hidden = true;
    }
    this.current.text += text;
    this.current.el.innerHTML = renderMarkdown(this.current.text); // renderer escapes all input
    this.scrollDown();
  }

  addMessage(role, text) {
    const el = document.createElement('div');
    el.className = `msg ${role}`;
    if (text) el.textContent = text;
    this.ui.log.append(el);
    this.scrollDown();
    return el;
  }

  scrollDown() {
    this.ui.log.scrollTop = this.ui.log.scrollHeight;
  }

  showError(message) {
    this.ui.error.textContent = message;
    this.ui.error.hidden = false;
  }

  hideError() {
    this.ui.error.hidden = true;
  }

  showApproval(frame) {
    const checked = validateApprovalRequired(frame);
    if (!checked.ok) {
      this.showError('Se recibió una solicitud de aprobación no válida.');
      return;
    }
    this.modal?.destroy();
    this.setBusy(false);
    const modal = createApprovalModal(document, checked.request, (payload) => {
      this.transport?.sendApproval(payload);
      modal.destroy();
      if (this.modal === modal) this.modal = null;
      this.ui.input.focus();
    });
    this.modal = modal;
    this.ui.panel.append(modal.element);
    if (this.ui.panel.hidden) this.toggle(true);
    modal.focusInitial();
  }
}

if (!customElements.get('ai-chat-agent')) customElements.define('ai-chat-agent', AiChatAgent);
