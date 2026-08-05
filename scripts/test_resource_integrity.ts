import assert from 'node:assert/strict';
import { createHash, generateKeyPairSync, sign } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { ResourceValidator } from '../electron/src/resource-validator.js';
import { generatePackagedManifest } from './generate_resource_manifest.js';

interface TestEntry {
  path: string;
  type: 'executable' | 'native-library' | 'model' | 'python-code' | 'plugin-data';
  size: number;
  sha256: string;
}

const { privateKey, publicKey } = generateKeyPairSync('ed25519');
const publicKeyDer = publicKey.export({ format: 'der', type: 'spki' });
const publicKeyBase64 = publicKeyDer.toString('base64');
const keyId = createHash('sha256').update(publicKeyDer).digest('hex');

async function writeSignedManifest(
  root: string,
  entries: TestEntry[],
  version = '1.1.2',
  platform: NodeJS.Platform = process.platform,
  architecture = process.arch,
): Promise<void> {
  const manifest = {
    schemaVersion: 1,
    product: {
      name: 'Akagi-NG',
      version,
      platform,
      architecture,
      pythonVersion: '3.12.13',
    },
    entries,
  };
  const bytes = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
  const signature = {
    schemaVersion: 1,
    algorithm: 'Ed25519',
    keyId,
    signature: sign(null, bytes, privateKey).toString('base64'),
  };
  const manifestRoot = platform === 'darwin' ? join(root, 'Resources') : root;
  await mkdir(manifestRoot, { recursive: true });
  await Promise.all([
    writeFile(join(manifestRoot, 'resource-manifest.json'), bytes),
    writeFile(
      join(manifestRoot, 'resource-manifest.sig'),
      `${JSON.stringify(signature)}\n`,
      'utf8',
    ),
  ]);
}

async function entry(root: string, path: string, type: TestEntry['type']): Promise<TestEntry> {
  const bytes = await readFile(join(root, ...path.split('/')));
  return {
    path,
    type,
    size: bytes.length,
    sha256: createHash('sha256').update(bytes).digest('hex'),
  };
}

function validator(
  root: string,
  expectedVersion = '1.1.2',
  platform: NodeJS.Platform = process.platform,
  architecture = process.arch,
): ResourceValidator {
  return new ResourceValidator(root, {
    enforceIntegrity: true,
    expectedVersion,
    platform,
    architecture,
    trustedPublicKeySpkiBase64: publicKeyBase64,
  });
}

async function main(): Promise<void> {
  const root = await mkdtemp(join(tmpdir(), 'akagi-resource-integrity-'));
  try {
    const nativeExtension = process.platform === 'win32' ? 'pyd' : 'so';
    const backend =
      process.platform === 'win32' ? 'bin/python/akagi-ng.exe' : 'bin/python/bin/akagi-ng';
    const stdlib =
      process.platform === 'win32' ? 'bin/python/Lib/os.py' : 'bin/python/lib/python3.12/os.py';
    const pythonRuntime =
      process.platform === 'win32'
        ? 'bin/python/python312.dll'
        : process.platform === 'darwin'
          ? 'bin/python/lib/libpython3.12.dylib'
          : 'bin/python/lib/libpython3.12.so.1.0';
    const files: Array<readonly [string, string, TestEntry['type']]> = [
      [backend, 'portable-backend', 'executable'],
      [`lib/libriichi.${nativeExtension}`, 'native-extension', 'native-library'],
      [`lib/libriichi3p.${nativeExtension}`, 'native-extension-3p', 'native-library'],
      [pythonRuntime, 'python-runtime', 'native-library'],
      ['bin/app_packages/vendor.dll', 'third-party-native-runtime', 'native-library'],
      ['models/mortal.pth', 'model-weights', 'model'],
      ['models/mortal3p.pth', 'three-player-model-weights', 'model'],
      ['bin/app_packages/akagi_ng/application.py', 'application-code', 'python-code'],
      ['bin/app_packages/akagi_ng/__main__.py', 'entrypoint-code', 'python-code'],
      ['bin/app_packages/cache.pyc', 'optional-bytecode', 'python-code'],
      ['bin/app_packages/archive.egg', 'importable-egg', 'python-code'],
      [stdlib, 'stdlib-code', 'python-code'],
      ['bin/python/python312.zip', 'stdlib-archive', 'python-code'],
      ['assets/plugins/majsoul-max/max_data.yaml', 'plugin-data', 'plugin-data'],
      ['assets/plugins/majsoul-max/metadata.json', 'plugin-metadata', 'plugin-data'],
    ];
    if (process.platform === 'win32') {
      files.push(
        ['assets/mihomo/windows-x64/mihomo.exe', 'mihomo', 'executable'],
        ['assets/privileged/AkagiNg.TunHelper.exe', 'tun-helper', 'executable'],
      );
    }
    for (const [path, contents] of files) {
      const filePath = join(root, ...path.split('/'));
      await mkdir(join(filePath, '..'), { recursive: true });
      await writeFile(filePath, contents, 'utf8');
    }
    const entries = await Promise.all(files.map(([path, , type]) => entry(root, path, type)));
    await writeSignedManifest(root, entries);

    const valid = await validator(root).validate();
    assert.equal(valid.integrity, 'valid');
    assert.equal(valid.verifiedFiles, entries.length);

    for (const protectedEntry of entries) {
      const path = join(root, ...protectedEntry.path.split('/'));
      const original = await readFile(path);
      await writeFile(path, Buffer.concat([original, Buffer.from('tampered')]));
      const result = await validator(root).validate();
      assert.equal(result.integrity, 'invalid', protectedEntry.path);
      assert.match(result.errors[0] ?? '', /size mismatch|SHA-256 mismatch/u);
      await writeFile(path, original);
    }

    const mutableSettingsPath = join(root, 'config', 'settings.json');
    await mkdir(join(mutableSettingsPath, '..'), { recursive: true });
    await writeFile(mutableSettingsPath, '{"theme":"dark"}\n', 'utf8');
    const mutableSettings = await validator(root).validate();
    assert.equal(mutableSettings.integrity, 'valid');

    for (const unlistedPath of [
      'bin/app_packages/injected.py',
      'bin/python/injected.zip',
      'bin/python/injected.dll',
      'assets/plugins/majsoul-max/injected.json',
    ]) {
      const path = join(root, ...unlistedPath.split('/'));
      await mkdir(join(path, '..'), { recursive: true });
      await writeFile(path, 'unlisted', 'utf8');
      const result = await validator(root).validate();
      assert.equal(result.integrity, 'invalid', unlistedPath);
      assert.match(result.errors[0] ?? '', /not listed in the signed manifest/u);
      await rm(path);
    }

    if (process.platform !== 'win32') {
      const alias = join(root, 'bin', 'python', 'runtime-alias.pc');
      await symlink('Lib/os.py', alias);
      const linkedRuntime = await validator(root).validate();
      assert.equal(linkedRuntime.integrity, 'invalid');
      assert.match(linkedRuntime.errors[0] ?? '', /Symlink found in protected resource tree/u);
      await rm(alias);
    }

    const originalManifest = await readFile(join(root, 'resource-manifest.json'), 'utf8');
    await writeFile(
      join(root, 'resource-manifest.json'),
      originalManifest.replace('"version": "1.1.2"', '"version": "9.9.9"'),
      'utf8',
    );
    const signatureTamper = await validator(root).validate();
    assert.equal(signatureTamper.integrity, 'invalid');
    assert.match(signatureTamper.errors[0] ?? '', /signature verification failed/u);

    await writeSignedManifest(root, entries, '9.9.9');
    const versionMismatch = await validator(root).validate();
    assert.equal(versionMismatch.integrity, 'invalid');
    assert.match(versionMismatch.errors[0] ?? '', /version does not match/u);

    await writeSignedManifest(root, [
      {
        path: '../escape.pth',
        type: 'model',
        size: 1,
        sha256: '0'.repeat(64),
      },
    ]);
    const traversal = await validator(root).validate();
    assert.equal(traversal.integrity, 'invalid');
    assert.match(traversal.errors[0] ?? '', /escapes its root/u);

    await writeSignedManifest(root, [
      {
        path: 'assets/plugins/majsoul-max/data.yaml',
        type: 'python-code',
        size: 1,
        sha256: '0'.repeat(64),
      },
    ]);
    const typeMismatch = await validator(root).validate();
    assert.equal(typeMismatch.integrity, 'invalid');
    assert.match(typeMismatch.errors[0] ?? '', /type mismatch/u);

    await writeSignedManifest(root, [
      {
        path: 'config/settings.json',
        type: 'plugin-data',
        size: 1,
        sha256: '0'.repeat(64),
      },
    ]);
    const mutablePathInManifest = await validator(root).validate();
    assert.equal(mutablePathInManifest.integrity, 'invalid');
    assert.match(mutablePathInManifest.errors[0] ?? '', /Unsupported protected resource path/u);

    await generatePackagedManifest(root, privateKey);
    const packageJson = JSON.parse(
      await readFile(join(__dirname, '..', 'package.json'), 'utf8'),
    ) as { version: string };
    const generated = await validator(root, packageJson.version).validate();
    assert.equal(generated.integrity, 'valid');
    assert.equal(generated.verifiedFiles, entries.length);
    const generatedManifest = JSON.parse(
      await readFile(join(root, 'resource-manifest.json'), 'utf8'),
    ) as { entries: TestEntry[] };
    const generatedTypes = new Map(
      generatedManifest.entries.map((manifestEntry) => [manifestEntry.path, manifestEntry.type]),
    );
    assert.equal(generatedTypes.get('assets/plugins/majsoul-max/max_data.yaml'), 'plugin-data');
    assert.equal(generatedTypes.get('bin/python/python312.zip'), 'python-code');
    assert.equal(generatedTypes.get('bin/app_packages/cache.pyc'), 'python-code');
    assert.equal(generatedTypes.get('bin/app_packages/vendor.dll'), 'native-library');
    assert.equal(generatedTypes.has('config/settings.json'), false);

    const darwinRoot = await mkdtemp(join(tmpdir(), 'akagi-resource-darwin-'));
    try {
      const darwinFiles: Array<readonly [string, string, TestEntry['type'] | undefined]> = [
        ['bin/app_packages/akagi_ng/application.py', 'application', 'python-code'],
        ['bin/app_packages/akagi_ng/__main__.py', 'entrypoint', 'python-code'],
        ['bin/python/lib/python3.12/os.py', 'stdlib', 'python-code'],
        ['assets/plugins/majsoul-max/max_data.yaml', 'plugin', 'plugin-data'],
        ['models/mortal.pth', 'model-4p', 'model'],
        ['models/mortal3p.pth', 'model-3p', 'model'],
        ['bin/python/bin/akagi-ng', 'backend', undefined],
        ['bin/python/lib/libpython3.12.dylib', 'python-runtime', undefined],
        ['lib/libriichi.so', 'native-4p', undefined],
        ['lib/libriichi3p.so', 'native-3p', undefined],
      ];
      for (const [path, contents] of darwinFiles) {
        const filePath = join(darwinRoot, ...path.split('/'));
        await mkdir(join(filePath, '..'), { recursive: true });
        await writeFile(filePath, contents, 'utf8');
      }
      const darwinEntries = await Promise.all(
        darwinFiles
          .filter((item): item is readonly [string, string, TestEntry['type']] => !!item[2])
          .map(([path, , type]) => entry(darwinRoot, path, type)),
      );
      await writeSignedManifest(darwinRoot, darwinEntries, '1.1.2', 'darwin', 'arm64');
      const darwinValidator = validator(darwinRoot, '1.1.2', 'darwin', 'arm64');
      assert.equal((await darwinValidator.validate()).integrity, 'valid');
      const darwinSignature = await readFile(
        join(darwinRoot, 'Resources', 'resource-manifest.sig'),
        'utf8',
      );
      assert.ok(darwinSignature.length > 0);
      await assert.rejects(readFile(join(darwinRoot, 'resource-manifest.sig'), 'utf8'), /ENOENT/u);

      const missingNative = join(darwinRoot, 'lib', 'libriichi3p.so');
      await rm(missingNative);
      const missingNativeResult = await darwinValidator.validate();
      assert.equal(missingNativeResult.integrity, 'invalid');
      assert.match(missingNativeResult.errors[0] ?? '', /ENOENT|Required macOS runtime/u);
    } finally {
      await rm(darwinRoot, { recursive: true, force: true });
    }
    console.log('✅ Resource integrity regression tests passed.');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

void main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});
