import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

import { extractFile } from '@electron/asar';

import {
  IPC_CHANNEL_ROLES,
  isAllowedGameUrl,
  isRoleAllowed,
  isSafeExternalUrl,
  isTrustedRendererUrl,
} from '../electron/src/security-policy';

const rootDir = resolve(__dirname, '..');
const source = (path: string) => readFileSync(join(rootDir, path), 'utf8');

const preloadSource = source('electron/src/preload.ts');
const ipcSource = source('electron/src/ipc-handlers.ts');
const windowSource = source('electron/src/window-manager.ts');
const indexSource = source('akagi_frontend/index.html');

function findTypeScriptFiles(directory: string): string[] {
  return readdirSync(join(rootDir, directory), { withFileTypes: true }).flatMap((entry) => {
    const relative = join(directory, entry.name);
    if (entry.isDirectory()) return findTypeScriptFiles(relative);
    return /\.tsx?$/.test(entry.name) ? [relative] : [];
  });
}

assert.equal(isTrustedRendererUrl('http://localhost:5173/#/', 'dashboard', false), true);
assert.equal(isTrustedRendererUrl('http://127.0.0.1:5173/#/hud', 'hud', false), true);
assert.equal(isTrustedRendererUrl('https://attacker.example/#/', 'dashboard', false), false);
assert.equal(
  isTrustedRendererUrl('file:///C:/app/dist/renderer/index.html#/hud', 'hud', true),
  true,
);
assert.equal(isTrustedRendererUrl('file:///C:/tmp/index.html#/', 'dashboard', true), false);
assert.equal(isTrustedRendererUrl('file:///C:/app/dist/renderer/index.html#/', 'hud', true), false);

assert.equal(isSafeExternalUrl('https://github.com/FanhuaAwA/Akagi-NG'), true);
assert.equal(isSafeExternalUrl('http://github.com/FanhuaAwA/Akagi-NG'), false);
assert.equal(isSafeExternalUrl('https://user:password@example.com/'), false);
assert.equal(isSafeExternalUrl('file:///C:/Windows/System32/calc.exe'), false);

const majsoulUrl = 'https://game.maj-soul.com/1/';
assert.equal(isAllowedGameUrl(majsoulUrl, 'majsoul', majsoulUrl), true);
assert.equal(
  isAllowedGameUrl('https://passport.mahjongsoul.com/login', 'majsoul', majsoulUrl),
  true,
);
assert.equal(isAllowedGameUrl('https://attacker.example/', 'majsoul', majsoulUrl), false);
assert.equal(
  isAllowedGameUrl('https://attacker.example/', 'majsoul', 'https://attacker.example/'),
  false,
);
assert.equal(isAllowedGameUrl('http://game.maj-soul.com/1/', 'majsoul', majsoulUrl), false);
assert.equal(isAllowedGameUrl('https://tenhou.net/3/', 'tenhou', 'https://tenhou.net/3/'), true);
assert.equal(
  isAllowedGameUrl('https://evil.tenhou.net.attacker.example/', 'tenhou', 'https://tenhou.net/3/'),
  false,
);
assert.equal(
  isAllowedGameUrl('https://attacker.example/', 'riichi_city', 'https://attacker.example/'),
  false,
);

assert.equal(isRoleAllowed('request-shutdown', 'dashboard'), true);
assert.equal(isRoleAllowed('request-shutdown', 'hud'), false);
assert.equal(isRoleAllowed('hud-set-click-through', 'hud'), true);
assert.equal(isRoleAllowed('hud-set-click-through', 'dashboard'), false);

assert.doesNotMatch(preloadSource, /^\s*(send|on|invoke):/m);
assert.doesNotMatch(preloadSource, /ipcRenderer\.send\(/);
assert.match(preloadSource, /requestShutdown: \(\) => ipcRenderer\.invoke\('request-shutdown'\)/);
assert.match(
  preloadSource,
  /getStartupConfig: \(\) => ipcRenderer\.invoke\('get-startup-config'\)/,
);

const registeredChannels = new Set(
  [...ipcSource.matchAll(/\bhandle\('([^']+)'/g)].map((match) => match[1]),
);
assert.deepEqual(registeredChannels, new Set(Object.keys(IPC_CHANNEL_ROLES)));
assert.equal((ipcSource.match(/ipcMain\.handle\(/g) ?? []).length, 1);
assert.match(ipcSource, /frame !== event\.sender\.mainFrame/);
assert.match(ipcSource, /isTrustedRendererUrl\(frame\.url, role, app\.isPackaged\)/);

assert.match(windowSource, /setWindowOpenHandler\(\(\) => \(\{ action: 'deny' \}\)\)/);
assert.match(windowSource, /will-frame-navigate/);
assert.match(windowSource, /will-attach-webview/);
assert.match(windowSource, /setPermissionRequestHandler/);
assert.match(windowSource, /sandbox: true/g);
assert.match(windowSource, /isAllowedGameUrl\(targetUrl, platform, url\)/);

assert.match(indexSource, /http-equiv="Content-Security-Policy"/);
assert.match(indexSource, /object-src 'none'/);
assert.match(indexSource, /frame-src 'none'/);
assert.match(indexSource, /script-src 'self'/);
assert.doesNotMatch(indexSource, /script-src[^;]*(unsafe-inline|unsafe-eval)/);
assert.doesNotMatch(indexSource, /<script>(.|\r|\n)*<\/script>/);

for (const path of findTypeScriptFiles('akagi_frontend/src')) {
  assert.doesNotMatch(source(path), /window\.electron\.(invoke|send|on)\(/);
}

if (process.argv.includes('--packaged')) {
  const asarPath = join(rootDir, 'dist', 'release', 'win-unpacked', 'resources', 'app.asar');
  assert.ok(existsSync(asarPath), 'Packaged app.asar was not found.');
  const packagedIndex = extractFile(asarPath, join('dist', 'renderer', 'index.html')).toString(
    'utf8',
  );
  const packagedPreload = extractFile(asarPath, join('dist', 'main', 'preload.js')).toString(
    'utf8',
  );
  const packagedMain = extractFile(asarPath, join('dist', 'main', 'ipc-handlers.js')).toString(
    'utf8',
  );
  assert.match(packagedIndex, /http-equiv="Content-Security-Policy"/);
  assert.match(packagedIndex, /script-src 'self'/);
  assert.doesNotMatch(packagedPreload, /ipcRenderer\.send\(/);
  assert.doesNotMatch(packagedPreload, /^\s*(send|on|invoke):/m);
  assert.match(packagedMain, /Unauthorized desktop request/);
  assert.match(packagedMain, /isTrustedRendererUrl/);
}

console.log('Electron trust-boundary regression tests passed.');
