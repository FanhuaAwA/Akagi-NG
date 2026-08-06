import assert from 'node:assert/strict';

import { createLogger } from '../electron/src/logger';

const originalWrite = process.stdout.write;
const brokenPipe = Object.assign(new Error('broken pipe'), { code: 'EPIPE' });

try {
  process.stdout.write = (() => {
    throw brokenPipe;
  }) as typeof process.stdout.write;

  assert.doesNotThrow(() => {
    createLogger('LoggerRegression').info('mihomo output after launcher pipe closed');
  });
} finally {
  process.stdout.write = originalWrite;
}

console.log('Logger broken-pipe regression test passed.');
