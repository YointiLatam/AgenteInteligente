export const STYLES = `
:host { --accent: #2563eb; --bg: #ffffff; --fg: #111827; --muted: #6b7280; --border: #e5e7eb; --bubble: #f3f4f6;
  position: fixed; bottom: 20px; right: 20px; z-index: 2147483000; font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--fg); }
:host([position="bottom-left"]) { right: auto; left: 20px; }
@media (prefers-color-scheme: dark) { :host { --bg: #1f2937; --fg: #f3f4f6; --muted: #9ca3af; --border: #374151; --bubble: #374151; } }
* { box-sizing: border-box; }
button { font: inherit; cursor: pointer; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.launcher { width: 56px; height: 56px; border-radius: 50%; border: 0; background: var(--accent); color: #fff; font-size: 24px;
  box-shadow: 0 4px 14px rgba(0,0,0,.25); transition: transform .15s ease; }
.launcher:hover { transform: scale(1.06); }
.panel { position: absolute; bottom: 68px; right: 0; width: 380px; max-width: calc(100vw - 40px); height: 560px; max-height: calc(100dvh - 110px);
  display: flex; flex-direction: column; background: var(--bg); border: 1px solid var(--border); border-radius: 14px;
  box-shadow: 0 12px 40px rgba(0,0,0,.28); overflow: hidden; animation: pop .18s ease; }
:host([position="bottom-left"]) .panel { right: auto; left: 0; }
.panel[hidden] { display: none; }
@keyframes pop { from { opacity: 0; transform: translateY(8px) scale(.98); } to { opacity: 1; transform: none; } }
header { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px; background: var(--accent); color: #fff; }
header h1 { font-size: 15px; margin: 0; font-weight: 600; }
.close { background: transparent; border: 0; color: inherit; font-size: 20px; line-height: 1; padding: 4px 8px; border-radius: 6px; }
.banner { padding: 8px 14px; font-size: 13px; }
.banner[hidden] { display: none; }
.banner.error { background: #fef2f2; color: #991b1b; }
.banner.reconnecting { background: #fffbeb; color: #92400e; }
.log { flex: 1; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 10px; }
.msg { max-width: 88%; padding: 8px 12px; border-radius: 12px; background: var(--bubble); overflow-wrap: anywhere; }
.msg.user { align-self: flex-end; background: var(--accent); color: #fff; white-space: pre-wrap; }
.msg.system { align-self: center; font-size: 12px; color: var(--muted); background: transparent; border: 1px dashed var(--border); }
.msg p { margin: 0 0 6px; } .msg p:last-child { margin: 0; }
.msg h1, .msg h2, .msg h3, .msg h4, .msg h5, .msg h6 { font-size: 15px; margin: 6px 0 4px; }
.msg pre { background: #0f172a; color: #e2e8f0; padding: 8px 10px; border-radius: 8px; overflow-x: auto; margin: 6px 0; }
.msg code { font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; }
.msg :not(pre) > code { background: rgba(127,127,127,.2); padding: 1px 4px; border-radius: 4px; }
.msg blockquote { margin: 6px 0; padding-left: 10px; border-left: 3px solid var(--border); color: var(--muted); }
.msg ul, .msg ol { margin: 4px 0; padding-left: 20px; }
.msg a { color: var(--accent); }
.typing { display: inline-flex; gap: 4px; padding: 10px 12px; }
.typing[hidden] { display: none; }
.typing span { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); animation: blink 1.2s infinite ease-in-out; }
.typing span:nth-child(2) { animation-delay: .15s; } .typing span:nth-child(3) { animation-delay: .3s; }
@keyframes blink { 0%, 80%, 100% { opacity: .25; } 40% { opacity: 1; } }
form { display: flex; gap: 8px; padding: 10px; border-top: 1px solid var(--border); }
textarea { flex: 1; resize: none; border: 1px solid var(--border); border-radius: 10px; padding: 8px 10px; font: inherit; background: var(--bg); color: var(--fg); max-height: 96px; }
.send { border: 0; border-radius: 10px; padding: 0 14px; background: var(--accent); color: #fff; }
.send:disabled { opacity: .5; cursor: not-allowed; }
.approval-overlay { position: absolute; inset: 0; background: rgba(0,0,0,.5); display: flex; align-items: center; justify-content: center; padding: 16px; z-index: 5; }
.approval-dialog { background: var(--bg); border-radius: 12px; padding: 16px; width: 100%; max-height: 100%; overflow-y: auto; }
.approval-dialog h2 { margin: 0 0 6px; font-size: 16px; }
.approval-dialog p { margin: 0 0 10px; color: var(--muted); }
.approval-params { margin: 0 0 12px; padding: 8px 10px; background: var(--bubble); border-radius: 8px; }
.approval-params dt { font-weight: 600; font-size: 12px; }
.approval-params dd { margin: 0 0 6px; white-space: pre-wrap; overflow-wrap: anywhere; font-family: ui-monospace, Menlo, monospace; font-size: 12.5px; }
.approval-actions { display: flex; justify-content: flex-end; gap: 8px; }
.approval-actions button { border-radius: 8px; padding: 8px 14px; border: 1px solid var(--border); }
.approval-actions .btn-primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.approval-actions .btn-secondary { background: transparent; color: var(--fg); }
.approval-actions button:disabled { opacity: .5; }
@media (max-width: 480px) {
  :host { inset: auto 16px 16px auto; }
  :host([position="bottom-left"]) { inset: auto auto 16px 16px; }
  .panel { position: fixed; inset: 0; width: 100vw; max-width: none; height: 100vh; height: 100dvh; max-height: none; border-radius: 0; border: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .panel, .launcher, .typing span { animation: none !important; transition: none !important; }
}
`;
