import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

const rootDir = resolve(__dirname, '..');
const source = (path: string) => readFileSync(join(rootDir, path), 'utf8');

function main(): void {
  const electronPackage = JSON.parse(source('electron/package.json')) as {
    scripts: Record<string, string>;
    build: {
      win: { requestedExecutionLevel: string };
      files: Array<string | { from: string; to: string; filter?: string[] }>;
      extraFiles: Array<{ from: string; to: string; filter?: string[] }>;
      extraResources: Array<{ from: string; to: string }>;
    };
  };
  const packageSource = source('scripts/package_desktop.ts');
  const managerSource = source('electron/src/mihomo-manager.ts');
  const mainSource = source('electron/src/main.ts');
  const ipcSource = source('electron/src/ipc-handlers.ts');
  const policySource = source('electron/src/security-policy.ts');

  assert.equal(electronPackage.build.win.requestedExecutionLevel, 'requireAdministrator');
  assert.match(electronPackage.scripts.package, /package_desktop/);
  assert.doesNotMatch(packageSource, /build_tun_helper|privileged/i);
  assert.ok(
    electronPackage.build.extraFiles.every(
      (entry) => entry.to !== 'assets/privileged' && !entry.from.includes('privileged'),
    ),
    'The removed TUN helper must not be packaged.',
  );
  const mainFiles = electronPackage.build.files.find(
    (entry) =>
      typeof entry !== 'string' && entry.from === '../dist/main' && entry.to === 'dist/main',
  );
  assert.ok(typeof mainFiles !== 'string' && mainFiles?.filter?.includes('!windows-tun-helper.js'));
  assert.ok(
    electronPackage.build.extraResources.some(
      (entry) => entry.from === '../LICENSE' && entry.to === 'LICENSE.txt',
    ),
  );
  assert.ok(
    electronPackage.build.extraResources.some(
      (entry) => entry.from === '../README.txt' && entry.to === 'README.txt',
    ),
  );

  assert.match(managerSource, /launchMihomoDirectly/);
  assert.match(managerSource, /spawn\([\s\S]*options\.binaryPath/);
  assert.match(managerSource, /\['-d', options\.workDir, '-f', options\.configPath\]/);
  assert.match(managerSource, /child\.kill\('SIGKILL'\)/);
  assert.doesNotMatch(
    managerSource,
    /launchWindowsTunHelper|WindowsTunSession|UAC approval|权限助手/,
  );
  assert.equal(existsSync(join(rootDir, 'electron', 'src', 'windows-tun-helper.ts')), false);
  assert.equal(existsSync(join(rootDir, 'electron', 'privileged-helper', 'TunHelper.cs')), false);
  assert.equal(
    existsSync(join(rootDir, 'electron', 'privileged-helper', 'AkagiNg.TunHelper.manifest')),
    false,
  );
  assert.equal(existsSync(join(rootDir, 'scripts', 'build_tun_helper.ts')), false);

  assert.ok(mainSource.indexOf('createDashboardWindow()') < mainSource.indexOf('startIfEnabled()'));
  assert.ok(
    mainSource.indexOf('mihomoManager.stop()') < mainSource.indexOf('backendManager.stop()'),
  );
  assert.doesNotMatch(ipcSource, /ipcMain\.handle\('mihomo-start'/);
  assert.match(ipcSource, /assertTrustedRenderer\(event, channel\)/);
  assert.match(ipcSource, /frame !== event\.sender\.mainFrame/);
  assert.match(policySource, /'mihomo-status': \['dashboard'\]/);
  assert.match(policySource, /'mihomo-reconcile': \['dashboard'\]/);
  assert.match(policySource, /'mihomo-stop': \['dashboard'\]/);

  console.log('✅ Direct mihomo TUN launch boundary tests passed.');
}

main();
