import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderMarkdown } from '../src/markdown.js';

const TAG = /<(?!\/?(p|br|strong|em|code|pre|a|ul|ol|li|blockquote|h[1-6])[\s>/])[a-z!]/i;

test('escapes script tags', () => {
  const html = renderMarkdown('<script>alert(1)</script>');
  assert.ok(!html.includes('<script'));
  assert.ok(html.includes('&lt;script&gt;'));
});

test('neutralizes onerror attributes in img tags', () => {
  const html = renderMarkdown('<img src=x onerror=alert(1)>');
  assert.ok(!html.includes('<img'));
  assert.doesNotMatch(html, TAG);
});

test('drops javascript: links but keeps the label', () => {
  const html = renderMarkdown('[click](javascript:alert(1))');
  assert.ok(!html.includes('<a'));
  assert.ok(!html.includes('href'));
  assert.ok(html.includes('click'));
});

test('drops data: and mixed-case javascript: links', () => {
  assert.ok(!renderMarkdown('[x](data:text/html;base64,AAAA)').includes('<a'));
  assert.ok(!renderMarkdown('[x](JaVaScRiPt:alert(1))').includes('<a'));
  assert.ok(!renderMarkdown('[x](vbscript:evil)').includes('<a'));
});

test('allows http, https and mailto with safe attributes', () => {
  for (const url of ['http://a.com', 'https://a.com/x?y=1&z=2', 'mailto:a@b.com']) {
    const html = renderMarkdown(`[t](${url})`);
    assert.match(html, /<a href="[^"]+" target="_blank" rel="noopener noreferrer">t<\/a>/);
  }
});

test('cannot break out of the href attribute with quotes', () => {
  const html = renderMarkdown('[x](https://a.com/"onmouseover="alert(1))');
  assert.ok(!html.includes('"onmouseover'));
  assert.doesNotMatch(html, /<a[^>]*\sonmouseover/);
});

test('nested HTML inside markdown stays inert', () => {
  const html = renderMarkdown('**<b onclick="x()">bold</b>** and *<iframe src=//evil></iframe>*');
  assert.doesNotMatch(html, TAG);
  assert.ok(html.includes('<strong>'));
});

test('HTML inside code is escaped', () => {
  const html = renderMarkdown('`<script>` and\n```\n<img onerror=1>\n```');
  assert.ok(html.includes('<code>&lt;script&gt;</code>'));
  assert.ok(html.includes('&lt;img onerror=1&gt;'));
});

test('unclosed fence does not leak markup and renders the rest as code', () => {
  const html = renderMarkdown('```js\n<script>x</script>\n**not bold**');
  assert.ok(html.startsWith('<pre><code>'));
  assert.ok(html.endsWith('</code></pre>'));
  assert.ok(!html.includes('<strong>'));
  assert.ok(!html.includes('<script'));
});

test('placeholder sequences in input cannot inject stashed html', () => {
  const html = renderMarkdown('`x` \u00000\u0000 [a](https://b.com)');
  assert.ok(!html.includes('\u0000'));
});

test('renders headings, emphasis, lists and blockquotes', () => {
  assert.equal(renderMarkdown('# Hola'), '<h1>Hola</h1>');
  assert.equal(renderMarkdown('**a** y *b*'), '<p><strong>a</strong> y <em>b</em></p>');
  assert.equal(renderMarkdown('- a\n- b'), '<ul><li>a</li><li>b</li></ul>');
  assert.equal(renderMarkdown('1. a\n2. b'), '<ol><li>a</li><li>b</li></ol>');
  assert.equal(renderMarkdown('> cita'), '<blockquote>cita</blockquote>');
});

test('handles empty and nullish input', () => {
  assert.equal(renderMarkdown(''), '');
  assert.equal(renderMarkdown(undefined), '');
});
