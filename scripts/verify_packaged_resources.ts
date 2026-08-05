import { existsSync } from 'node:fs';
import { readdir, readFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

import { ResourceValidator } from '../electron/src/resource-validator.js';

async function detectPackagedRoot(rootDir: string): Promise<string> {
  const releaseDir = join(rootDir, 'dist', 'release');
  if (process.platform === 'win32') return join(releaseDir, 'win-unpacked');
  if (process.platform === 'linux') return join(releaseDir, 'linux-unpacked');

  const directories = await readdir(releaseDir, { withFileTypes: true });
  for (const directory of directories) {
    if (!directory.isDirectory() || !directory.name.startsWith('mac')) continue;
    const contents = join(releaseDir, directory.name, 'Akagi-NG.app', 'Contents');
    if (existsSync(contents)) return contents;
  }
  throw new Error(`Could not locate the packaged macOS application under ${releaseDir}.`);
}

async function main(): Promise<void> {
  const rootDir = resolve(__dirname, '..');
  const marker = process.argv.indexOf('--root');
  const packagedRootArgument = marker >= 0 ? process.argv[marker + 1] : undefined;
  const packagedRoot = packagedRootArgument
    ? resolve(packagedRootArgument)
    : await detectPackagedRoot(rootDir);

  const [compiledAnchor, packageJsonText] = await Promise.all([
    readFile(join(rootDir, 'dist', 'main', 'resource-trust-anchor.js'), 'utf8'),
    readFile(join(rootDir, 'package.json'), 'utf8'),
  ]);
  const match = compiledAnchor.match(/RESOURCE_PUBLIC_KEY_SPKI_BASE64\s*=\s*'([^']+)'/u);
  if (!match?.[1] || match[1] === 'AKAGI_RESOURCE_PUBLIC_KEY_PLACEHOLDER') {
    throw new Error('The compiled resource trust anchor was not injected.');
  }
  const packageJson = JSON.parse(packageJsonText) as { version?: unknown };
  if (typeof packageJson.version !== 'string') throw new Error('Root package version is missing.');

  const result = await new ResourceValidator(packagedRoot, {
    enforceIntegrity: true,
    expectedVersion: packageJson.version,
    trustedPublicKeySpkiBase64: match[1],
  }).validate();
  if (result.integrity !== 'valid') {
    throw new Error(result.errors.join('; ') || 'Packaged resource integrity validation failed.');
  }
  console.log(
    `✅ Packaged manifest verified ${result.verifiedFiles} resources (${result.verifiedBytes} bytes).`,
  );
}

void main().catch((error: unknown) => {
  console.error('❌ Packaged resource verification failed:', error);
  process.exitCode = 1;
});
