import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

import { buildMihomoConfig } from '../electron/src/mihomo-config';
import {
  buildElevationCommand,
  encodeStartCommand,
  parseHelperMessage,
} from '../electron/src/windows-tun-helper';

const rootDir = resolve(__dirname, '..');
const electronPackagePath = join(rootDir, 'electron', 'package.json');
const helperManifestPath = join(
  rootDir,
  'electron',
  'privileged-helper',
  'AkagiNg.TunHelper.manifest',
);
const helperSourcePath = join(rootDir, 'electron', 'privileged-helper', 'TunHelper.cs');
const helperBinaryPath = join(rootDir, 'build', 'privileged', 'AkagiNg.TunHelper.exe');
const mihomoPath = join(rootDir, 'assets', 'mihomo', 'windows-x64', 'mihomo.exe');

function sha256(path: string): string {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

function findManifestTool(): string | null {
  const programFilesX86 = process.env['ProgramFiles(x86)'];
  if (!programFilesX86) return null;
  const binRoot = join(programFilesX86, 'Windows Kits', '10', 'bin');
  if (!existsSync(binRoot)) return null;
  const versions = readdirSync(binRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort()
    .reverse();
  for (const version of versions) {
    const candidate = join(binRoot, version, 'x64', 'mt.exe');
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

function findCSharpCompiler(): string | null {
  const windowsRoot = process.env.WINDIR ?? 'C:\\Windows';
  for (const framework of ['Framework64', 'Framework']) {
    const candidate = join(windowsRoot, 'Microsoft.NET', framework, 'v4.0.30319', 'csc.exe');
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

function testHelperConfigValidation(): void {
  if (process.platform !== 'win32') return;
  const compiler = findCSharpCompiler();
  assert.ok(compiler, 'The C# compiler required by the helper test was not found.');
  const tempDirectory = mkdtempSync(join(tmpdir(), 'akagi-helper-test-'));
  try {
    const testExecutable = join(tempDirectory, 'TunHelper.Test.exe');
    const compileResult = spawnSync(
      compiler,
      [
        '/nologo',
        '/target:exe',
        '/platform:x64',
        '/define:AKAGI_HELPER_TEST',
        '/reference:System.Web.Extensions.dll',
        `/out:${testExecutable}`,
        helperSourcePath,
      ],
      { encoding: 'utf8', windowsHide: true },
    );
    assert.equal(compileResult.status, 0, `${compileResult.stdout}\n${compileResult.stderr}`);

    const validConfig = buildMihomoConfig({
      mitmHost: '127.0.0.1',
      mitmPort: 6789,
      mixedPort: 7890,
      controllerPort: 9090,
      strictRoute: false,
      secret: 'a'.repeat(48),
    });
    const validPath = join(tempDirectory, 'valid.json');
    writeFileSync(validPath, JSON.stringify(validConfig), 'utf8');
    assert.equal(spawnSync(testExecutable, ['--validate-config', validPath]).status, 0);

    const invalidPath = join(tempDirectory, 'invalid.json');
    writeFileSync(
      invalidPath,
      JSON.stringify({ ...validConfig, 'external-ui': 'C:\\temp' }),
      'utf8',
    );
    assert.equal(spawnSync(testExecutable, ['--validate-config', invalidPath]).status, 2);

    const remoteControllerPath = join(tempDirectory, 'remote-controller.json');
    writeFileSync(
      remoteControllerPath,
      JSON.stringify({ ...validConfig, 'external-controller': '0.0.0.0:9090' }),
      'utf8',
    );
    assert.equal(spawnSync(testExecutable, ['--validate-config', remoteControllerPath]).status, 2);
  } finally {
    rmSync(tempDirectory, { recursive: true, force: true });
  }
}

function extractManifest(executable: string): string | null {
  const manifestTool = findManifestTool();
  if (!manifestTool || !existsSync(executable)) return null;
  const tempDirectory = mkdtempSync(join(tmpdir(), 'akagi-manifest-'));
  const outputPath = join(tempDirectory, 'manifest.xml');
  try {
    execFileSync(manifestTool, [
      '-nologo',
      `-inputresource:${executable};#1`,
      `-out:${outputPath}`,
    ]);
    return readFileSync(outputPath, 'utf8');
  } finally {
    rmSync(tempDirectory, { recursive: true, force: true });
  }
}

function assertAuthenticodePolicy(path: string): void {
  if (process.platform !== 'win32' || !existsSync(path)) return;
  const command = `(Get-AuthenticodeSignature -LiteralPath $env:AKAGI_SIGNATURE_TARGET).Status.ToString()`;
  const result = spawnSync('powershell.exe', ['-NoLogo', '-NoProfile', '-Command', command], {
    env: { ...process.env, AKAGI_SIGNATURE_TARGET: path },
    encoding: 'utf8',
    windowsHide: true,
  });
  const status = result.stdout.trim();
  if (process.env.AKAGI_REQUIRE_CODE_SIGNING === '1') {
    assert.equal(status, 'Valid', 'Release policy requires a valid Authenticode signature.');
  } else {
    console.log(
      `ℹ️ Helper Authenticode status: ${status || 'Unknown'} (enforced in signed release mode)`,
    );
  }
}

function main(): void {
  const electronPackage = JSON.parse(readFileSync(electronPackagePath, 'utf8')) as {
    scripts: Record<string, string>;
    build: {
      afterPack?: string;
      afterSign?: string;
      win: { requestedExecutionLevel: string };
      extraFiles: Array<{ from: string; to: string; filter?: string[] }>;
    };
  };
  assert.equal(electronPackage.build.win.requestedExecutionLevel, 'asInvoker');
  assert.deepEqual((electronPackage.build.win as { signExts?: string[] }).signExts, [
    '!mihomo.exe',
  ]);
  assert.match(electronPackage.scripts.package, /package_desktop/);
  assert.equal(electronPackage.build.afterPack, '../scripts/after_pack_resource_manifest.cjs');
  assert.equal(electronPackage.build.afterSign, '../scripts/after_sign_resource_manifest.cjs');
  const packageSource = readFileSync(join(rootDir, 'scripts', 'package_desktop.ts'), 'utf8');
  assert.match(packageSource, /build_tun_helper\.ts/);
  assert.ok(
    electronPackage.build.extraFiles.some(
      (entry) =>
        entry.to === 'assets/privileged' && entry.filter?.includes('AkagiNg.TunHelper.exe'),
    ),
  );
  assert.ok(
    electronPackage.build.extraFiles.some(
      (entry) => entry.from === '../LICENSE' && entry.to === 'LICENSE.txt',
    ),
    'The public license must use a .txt target so macOS codesign does not treat it as nested code.',
  );
  assert.ok(
    electronPackage.build.extraFiles.every((entry) => entry.to !== 'LICENSE'),
    'Do not place an extensionless LICENSE directly in the macOS Contents directory.',
  );

  const sourceManifest = readFileSync(helperManifestPath, 'utf8');
  assert.match(sourceManifest, /requestedExecutionLevel level="requireAdministrator"/);
  const embeddedHelperManifest = extractManifest(helperBinaryPath);
  if (embeddedHelperManifest) {
    assert.match(embeddedHelperManifest, /requestedExecutionLevel level="requireAdministrator"/);
  }

  const helperSource = readFileSync(helperSourcePath, 'utf8');
  const pinnedHash = helperSource.match(/ExpectedMihomoSha256 = "([a-f0-9]{64})"/)?.[1];
  assert.ok(pinnedHash, 'The privileged helper must pin the mihomo SHA-256.');
  assert.equal(sha256(mihomoPath), pinnedHash);
  assert.match(helperSource, /CommonApplicationData/);
  assert.match(helperSource, /JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE|0x00002000/);
  assert.match(helperSource, /SetAccessRuleProtection\(true, false\)/);
  assert.match(helperSource, /RequireExactKeys/);
  testHelperConfigValidation();

  const hello = parseHelperMessage('HELLO\t1');
  assert.deepEqual(hello, { type: 'hello', version: 1 });
  assert.deepEqual(parseHelperMessage('STARTED\t42'), { type: 'started', pid: 42 });
  assert.deepEqual(parseHelperMessage(`ERROR\t${Buffer.from('denied').toString('base64')}`), {
    type: 'error',
    message: 'denied',
  });
  assert.throws(() => parseHelperMessage('HELLO\t2'), /Unsupported/);
  assert.throws(() => parseHelperMessage('STARTED\t-1'), /process id/);

  const startCommand = encodeStartCommand('C:\\含 空格\\work', 'C:\\含 空格\\work\\config.yaml');
  assert.equal(startCommand.split('\t').length, 3);
  assert.ok(!startCommand.includes('含 空格'));
  const elevationCommand = buildElevationCommand();
  assert.match(elevationCommand, /AKAGI_TUN_HELPER_PATH/);
  assert.match(elevationCommand, /AKAGI_TUN_PIPE_NAME/);
  assert.doesNotMatch(elevationCommand, /config|mihomo\.exe/i);

  const managerSource = readFileSync(join(rootDir, 'electron', 'src', 'mihomo-manager.ts'), 'utf8');
  assert.match(managerSource, /if \(!config\.mihomo\.enabled\)/);
  assert.match(managerSource, /launchWindowsTunHelper/);
  assert.doesNotMatch(managerSource, /this\.process\s*=\s*spawn/);

  const mainSource = readFileSync(join(rootDir, 'electron', 'src', 'main.ts'), 'utf8');
  assert.ok(mainSource.indexOf('createDashboardWindow()') < mainSource.indexOf('startIfEnabled()'));
  assert.ok(
    mainSource.indexOf('mihomoManager.stop()') < mainSource.indexOf('backendManager.stop()'),
  );

  const ipcSource = readFileSync(join(rootDir, 'electron', 'src', 'ipc-handlers.ts'), 'utf8');
  assert.doesNotMatch(ipcSource, /ipcMain\.handle\('mihomo-start'/);
  assert.match(ipcSource, /assertTrustedRenderer\(event, channel\)/);
  assert.match(ipcSource, /frame !== event\.sender\.mainFrame/);
  const policySource = readFileSync(join(rootDir, 'electron', 'src', 'security-policy.ts'), 'utf8');
  assert.match(policySource, /'mihomo-status': \['dashboard'\]/);
  assert.match(policySource, /'mihomo-reconcile': \['dashboard'\]/);
  assert.match(policySource, /'mihomo-stop': \['dashboard'\]/);

  const packagedExecutable = join(rootDir, 'dist', 'release', 'win-unpacked', 'Akagi-NG.exe');
  if (process.env.AKAGI_VERIFY_PACKAGED_MANIFEST === '1') {
    const appManifest = extractManifest(packagedExecutable);
    assert.ok(appManifest, 'The packaged Windows executable or Windows manifest tool is missing.');
    assert.match(appManifest, /requestedExecutionLevel level="asInvoker"/);
  }
  const packagedHelper = join(
    rootDir,
    'dist',
    'release',
    'win-unpacked',
    'assets',
    'privileged',
    'AkagiNg.TunHelper.exe',
  );
  if (process.env.AKAGI_VERIFY_PACKAGED_MANIFEST === '1') {
    const packagedHelperManifest = extractManifest(packagedHelper);
    assert.ok(packagedHelperManifest, 'The packaged TUN helper manifest is missing.');
    assert.match(packagedHelperManifest, /requestedExecutionLevel level="requireAdministrator"/);
  }
  assertAuthenticodePolicy(
    process.env.AKAGI_VERIFY_PACKAGED_MANIFEST === '1' ? packagedHelper : helperBinaryPath,
  );

  if (process.env.AKAGI_VERIFY_PACKAGED_MANIFEST === '1') {
    const packagedMihomo = join(
      rootDir,
      'dist',
      'release',
      'win-unpacked',
      'assets',
      'mihomo',
      'windows-x64',
      'mihomo.exe',
    );
    assert.equal(sha256(packagedMihomo), pinnedHash);
  }

  console.log('✅ DE-PRIV-001 privilege-boundary tests passed.');
}

main();
