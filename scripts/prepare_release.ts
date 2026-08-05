import { copyFileSync, existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { platform as getPlatform } from 'node:os';
import { join, resolve } from 'node:path';

const rootDir = resolve(__dirname, '../');
const extraDir = join(rootDir, 'build', 'extra');

// 0. Ensure extraDir exists
if (existsSync(extraDir)) {
  rmSync(extraDir, { recursive: true, force: true });
}
mkdirSync(extraDir, { recursive: true });

console.log('📦 Preparing release assets...');

// 1. Create target folders
['lib', 'models', 'logs', 'config'].forEach((folder) => {
  const folderPath = join(extraDir, folder);
  if (!existsSync(folderPath)) {
    mkdirSync(folderPath);
  }
});

// 2. Bundle Models
['mortal.pth', 'mortal3p.pth', 'LICENSE'].forEach((modelFile) => {
  const src = join(rootDir, 'models', modelFile);
  if (!existsSync(src)) {
    throw new Error(`Required model asset is missing: ${src}`);
  }
  copyFileSync(src, join(extraDir, 'models', modelFile));
  console.log(`   ✅ Bundled model: ${modelFile}`);
});

// 3. Bundle and rename libriichi for current platform
const platform = getPlatform();

const sysStr =
  platform === 'win32'
    ? 'pc-windows-msvc'
    : platform === 'darwin'
      ? 'apple-darwin'
      : 'unknown-linux-gnu';
const ext = platform === 'win32' ? 'pyd' : 'so';
const archStr = platform === 'darwin' ? 'aarch64' : 'x86_64';

['libriichi', 'libriichi3p'].forEach((prefix) => {
  const pattern = `${prefix}-3.12-${archStr}-${sysStr}.${ext}`;
  const srcFile = join(rootDir, 'lib', pattern);
  if (existsSync(srcFile)) {
    copyFileSync(srcFile, join(extraDir, 'lib', `${prefix}.${ext}`));
    console.log(`   ✅ Bundled lib: ${prefix}.${ext} (from ${pattern})`);
  } else {
    // try fallback
    const fallbackSrc = join(rootDir, 'lib', `${prefix}.${ext}`);
    if (existsSync(fallbackSrc)) {
      copyFileSync(fallbackSrc, join(extraDir, 'lib', `${prefix}.${ext}`));
      console.log(`   ✅ Bundled lib: ${prefix}.${ext} (from fallback exact match)`);
    } else {
      throw new Error(`Required native library is missing: ${pattern}`);
    }
  }
});

// 4. Copy lib/LICENSE
const libLicense = join(rootDir, 'lib', 'LICENSE');
if (existsSync(libLicense)) {
  copyFileSync(libLicense, join(extraDir, 'lib', 'LICENSE'));
  console.log('   ✅ Bundled lib: LICENSE');
}

// 5. Config/Logs placeholders
['logs', 'config'].forEach((folder) => {
  writeFileSync(join(extraDir, folder, '_placeholder'), '');
});

console.log('✅ Release assets prepared in build/extra');
