#!/usr/bin/env node
/**
 * Copy Excalidraw fonts/wasm from node_modules into public/ for self-hosting.
 *
 * Why: window.EXCALIDRAW_ASSET_PATH is set to '/excalidraw-assets/' so the
 * editor can run on intranet deployments without reaching unpkg / CDN.
 *
 * Source: node_modules/@excalidraw/excalidraw/dist/prod/
 * Target: public/excalidraw-assets/
 *
 * Idempotent: copies recursively, overwrites existing files. Skip if source
 * doesn't exist (e.g. before `pnpm install`).
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const SRC = path.join(ROOT, 'node_modules', '@excalidraw', 'excalidraw', 'dist', 'prod');
const DST = path.join(ROOT, 'public', 'excalidraw-assets');

function copyDir(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const s = path.join(src, entry.name);
    const d = path.join(dst, entry.name);
    if (entry.isDirectory()) copyDir(s, d);
    else fs.copyFileSync(s, d);
  }
}

if (!fs.existsSync(SRC)) {
  console.log('[copy-excalidraw-assets] skip (source not found, run after pnpm install)');
  process.exit(0);
}

copyDir(SRC, DST);
console.log(`[copy-excalidraw-assets] copied ${SRC} -> ${DST}`);
