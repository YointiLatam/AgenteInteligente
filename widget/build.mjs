import { build } from 'esbuild';

await build({
  entryPoints: ['src/ai-chat-agent.js'],
  outfile: 'dist/ai-chat-agent.js',
  bundle: true,
  minify: true,
  format: 'iife',
  target: ['es2020'],
  legalComments: 'none',
});
console.log('Generado dist/ai-chat-agent.js');
