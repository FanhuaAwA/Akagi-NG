import { spawnSync } from 'node:child_process';
import { createPublicKey } from 'node:crypto';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';

import { injectTrustAnchor, loadSigningKey } from './generate_resource_manifest.js';

const rootDir = resolve(__dirname, '..');
const electronDir = resolve(rootDir, 'electron');
const resolveModule = createRequire(__filename).resolve;

function runNode(
  args: string[],
  env: NodeJS.ProcessEnv = process.env,
  cwd: string = rootDir,
): void {
  const result = spawnSync(process.execPath, args, {
    cwd,
    env,
    stdio: 'inherit',
    shell: false,
  });
  if (result.error) throw result.error;
  if (result.status !== 0)
    throw new Error(`Node child process failed with ${String(result.status)}.`);
}

async function main(): Promise<void> {
  const tsxCli = resolveModule('tsx/cli');
  runNode([tsxCli, resolve(rootDir, 'scripts', 'build_tun_helper.ts')]);

  const { privateKey, ephemeral } = loadSigningKey();
  const publicKeyDer = createPublicKey(privateKey).export({ format: 'der', type: 'spki' });
  await injectTrustAnchor(publicKeyDer.toString('base64'));

  const effectiveKey = privateKey.export({ format: 'pem', type: 'pkcs8' }).toString();
  runNode(
    [resolveModule('electron-builder/out/cli/cli.js'), '--publish', 'never'],
    {
      ...process.env,
      AKAGI_RESOURCE_EFFECTIVE_SIGNING_KEY: effectiveKey,
    },
    electronDir,
  );

  if (ephemeral) {
    console.warn(
      '⚠️ AKAGI_RESOURCE_SIGNING_KEY is unset; this build uses a one-time Ed25519 key. Configure the release secret for stable provenance.',
    );
  }
}

void main().catch((error: unknown) => {
  console.error('❌ Desktop packaging failed:', error);
  process.exitCode = 1;
});
