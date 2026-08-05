import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';

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
  runNode(
    [resolveModule('electron-builder/out/cli/cli.js'), '--publish', 'never'],
    process.env,
    electronDir,
  );
}

void main().catch((error: unknown) => {
  console.error('❌ Desktop packaging failed:', error);
  process.exitCode = 1;
});
