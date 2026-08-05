/* eslint-disable @typescript-eslint/no-require-imports, no-undef */

const { spawnSync } = require('node:child_process');
const { join, resolve } = require('node:path');

module.exports = async function afterPackResourceManifest(context) {
  // Windows emits afterSign even for an unsigned local build because electron-builder
  // edits the executable resources. Waiting for that phase avoids hashing stale bytes.
  // On macOS the generator writes detached metadata under Contents/Resources so
  // codesign seals it as data rather than treating it as a nested code object.
  if (context.electronPlatformName === 'win32') return;

  const rootDir = resolve(__dirname, '..');
  const tsxCli = require.resolve('tsx/cli');
  const generator = join(rootDir, 'scripts', 'generate_resource_manifest.ts');
  const packagedRoot =
    context.electronPlatformName === 'darwin'
      ? join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`, 'Contents')
      : context.appOutDir;
  const result = spawnSync(process.execPath, [tsxCli, generator, '--packaged-root', packagedRoot], {
    cwd: rootDir,
    env: process.env,
    stdio: 'inherit',
    shell: false,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`Pre-sign resource manifest generation failed with ${String(result.status)}.`);
  }
};
