import assert from 'node:assert/strict';
import { createHash, generateKeyPairSync, sign } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { ResourceValidator } from '../electron/src/resource-validator.js';

interface TestEntry {
  path: string;
  type: 'executable' | 'native-library' | 'model';
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
): Promise<void> {
  const manifest = {
    schemaVersion: 1,
    product: {
      name: 'Akagi-NG',
      version,
      platform: process.platform,
      architecture: process.arch,
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
  await Promise.all([
    writeFile(join(root, 'resource-manifest.json'), bytes),
    writeFile(join(root, 'resource-manifest.sig'), `${JSON.stringify(signature)}\n`, 'utf8'),
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

function validator(root: string, expectedVersion = '1.1.2'): ResourceValidator {
  return new ResourceValidator(root, {
    enforceIntegrity: true,
    expectedVersion,
    trustedPublicKeySpkiBase64: publicKeyBase64,
  });
}

async function main(): Promise<void> {
  const root = await mkdtemp(join(tmpdir(), 'akagi-resource-integrity-'));
  try {
    const files = [
      ['bin/python/akagi-ng.exe', 'portable-backend', 'executable'],
      ['lib/libriichi.pyd', 'native-extension', 'native-library'],
      ['models/mortal.pth', 'model-weights', 'model'],
    ] as const;
    for (const [path, contents] of files) {
      const filePath = join(root, ...path.split('/'));
      await mkdir(join(filePath, '..'), { recursive: true });
      await writeFile(filePath, contents, 'utf8');
    }
    const entries = await Promise.all(files.map(([path, , type]) => entry(root, path, type)));
    await writeSignedManifest(root, entries);

    const valid = await validator(root).validate();
    assert.equal(valid.integrity, 'valid');
    assert.equal(valid.verifiedFiles, 3);

    for (const protectedEntry of entries) {
      const path = join(root, ...protectedEntry.path.split('/'));
      const original = await readFile(path);
      await writeFile(path, Buffer.concat([original, Buffer.from('tampered')]));
      const result = await validator(root).validate();
      assert.equal(result.integrity, 'invalid', protectedEntry.path);
      assert.match(result.errors[0] ?? '', /size mismatch|SHA-256 mismatch/u);
      await writeFile(path, original);
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

    console.log('✅ Resource integrity regression tests passed.');
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

void main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});
