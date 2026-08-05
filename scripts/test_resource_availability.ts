import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { ResourceChecker } from '../electron/src/resource-checker.js';

async function main(): Promise<void> {
  const root = await mkdtemp(join(tmpdir(), 'akagi-resource-availability-'));
  try {
    const libDir = join(root, 'lib');
    const modelsDir = join(root, 'models');
    await Promise.all([mkdir(libDir), mkdir(modelsDir)]);

    const checker = new ResourceChecker(root, { platform: 'win32' });
    assert.deepEqual(await checker.check(), {
      lib: false,
      models: false,
      missingCritical: ['lib'],
      missingOptional: ['models'],
    });

    await Promise.all([
      writeFile(join(libDir, 'libriichi.pyd'), ''),
      writeFile(join(libDir, 'libriichi3p.pyd'), ''),
      writeFile(join(modelsDir, 'mortal.pth'), ''),
    ]);
    assert.deepEqual(await checker.check(), {
      lib: true,
      models: true,
      missingCritical: [],
      missingOptional: [],
    });
  } finally {
    await rm(root, { recursive: true, force: true });
  }

  console.log('Fast resource availability checks passed.');
}

void main().catch((error: unknown) => {
  console.error(error);
  process.exitCode = 1;
});
