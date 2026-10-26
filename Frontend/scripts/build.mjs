import { build } from 'esbuild';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const root = new URL('../', import.meta.url);
const result = await build({
  absWorkingDir: fileURLToPath(root),
  entryPoints: ['src/main.tsx'],
  bundle: true,
  write: false,
  outfile: 'login.js',
  format: 'iife',
  platform: 'browser',
  target: ['es2022'],
  minify: true,
  define: { 'process.env.NODE_ENV': '"production"' },
  legalComments: 'inline',
});

const javascript = result.outputFiles.find((file) => file.path.endsWith('.js')).text;
const css = result.outputFiles.find((file) => file.path.endsWith('.css')).text;
const html = `<!doctype html>
<!-- Generated from src/. Run npm run build after editing the React source. -->
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>로그인 | Paint Rush</title>
  <style>${css}</style>
</head>
<body>
  <div id="root"></div>
  <script>${javascript.replace(/<\/script/gi, '<\\/script')}</script>
</body>
</html>
`;

await mkdir(new URL('dist/', root), { recursive: true });
await writeFile(new URL('index.html', root), html);
await writeFile(new URL('dist/index.html', root), html);
console.log('Built standalone index.html and dist/index.html. No server required.');
