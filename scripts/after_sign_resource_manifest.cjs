/* eslint-disable @typescript-eslint/no-require-imports, no-undef */

const { spawnSync } = require('node:child_process');
const { join, resolve } = require('node:path');

module.exports = async function afterSignResourceManifest(context) {
  // macOS embeds the manifest before codesign via afterPack; changing the bundle
  // here would invalidate its seal. Linux does not emit afterSign when unsigned.
  if (context.electronPlatformName !== 'win32') return;

  const rootDir = resolve(__dirname, '..');
  const tsxCli = require.resolve('tsx/cli');
  const generator = join(rootDir, 'scripts', 'generate_resource_manifest.ts');
  const result = spawnSync(
    process.execPath,
    [tsxCli, generator, '--packaged-root', context.appOutDir],
    {
      cwd: rootDir,
      env: process.env,
      stdio: 'inherit',
      shell: false,
    },
  );
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`Post-sign resource manifest generation failed with ${String(result.status)}.`);
  }
};
