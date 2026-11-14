// Safe markdown renderer: every piece of input is HTML-escaped before any
// markup is produced, and only whitelisted tags are ever emitted.

const SAFE_URL = /^(https?:\/\/|mailto:)[^\s]*$/i;

export function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Renders inline markup. `text` must already be escaped. */
function renderInline(text) {
  const stash = [];
  const hold = (html) => {
    stash.push(html);
    return `\u0000${stash.length - 1}\u0000`;
  };

  let out = text.replace(/`([^`\n]+)`/g, (_, code) => hold(`<code>${code}</code>`));

  out = out.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (match, label, url) => {
    if (!SAFE_URL.test(url)) return label;
    return hold(`<a href="${url}" target="_blank" rel="noopener noreferrer">${emphasis(label)}</a>`);
  });

  out = emphasis(out);
  // eslint-disable-next-line no-control-regex
  return out.replace(/\u0000(\d+)\u0000/g, (_, i) => stash[Number(i)]);
}

function emphasis(text) {
  return text
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_\n]+)__/g, '<strong>$1</strong>')
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, '$1<em>$2</em>')
    .replace(/(^|[^_\w])_([^_\n]+)_(?!\w)/g, '$1<em>$2</em>');
}

export function renderMarkdown(source) {
  // NUL is reserved for internal placeholders; drop it from user input.
  const lines = String(source ?? '')
    // eslint-disable-next-line no-control-regex
    .replace(/\u0000/g, '')
    .replace(/\r\n?/g, '\n')
    .split('\n');
  const html = [];
  let paragraph = [];
  let list = null; // { tag, items }
  let quote = [];

  const flushParagraph = () => {
    if (paragraph.length) html.push(`<p>${paragraph.map(renderInline).join('<br>')}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (list) {
      html.push(`<${list.tag}>${list.items.map((i) => `<li>${renderInline(i)}</li>`).join('')}</${list.tag}>`);
    }
    list = null;
  };
  const flushQuote = () => {
    if (quote.length) html.push(`<blockquote>${quote.map(renderInline).join('<br>')}</blockquote>`);
    quote = [];
  };
  const flushAll = () => {
    flushParagraph();
    flushList();
    flushQuote();
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    if (/^```/.test(line)) {
      flushAll();
      const code = [];
      i += 1;
      // An unclosed fence swallows the rest of the text as code.
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        code.push(lines[i]);
        i += 1;
      }
      html.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`);
      i += 1;
      continue;
    }

    const esc = escapeHtml(line);
    let m;
    if (line.trim() === '') {
      flushAll();
    } else if ((m = /^(#{1,6})\s+(.*)$/.exec(esc))) {
      flushAll();
      html.push(`<h${m[1].length}>${renderInline(m[2])}</h${m[1].length}>`);
    } else if ((m = /^&gt;\s?(.*)$/.exec(esc))) {
      flushParagraph();
      flushList();
      quote.push(m[1]);
    } else if ((m = /^\s*[-*+]\s+(.*)$/.exec(esc))) {
      flushParagraph();
      flushQuote();
      if (list && list.tag !== 'ul') flushList();
      list ??= { tag: 'ul', items: [] };
      list.items.push(m[1]);
    } else if ((m = /^\s*\d+[.)]\s+(.*)$/.exec(esc))) {
      flushParagraph();
      flushQuote();
      if (list && list.tag !== 'ol') flushList();
      list ??= { tag: 'ol', items: [] };
      list.items.push(m[1]);
    } else {
      flushList();
      flushQuote();
      paragraph.push(esc);
    }
    i += 1;
  }
  flushAll();
  return html.join('');
}
