import { spawnSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync } from 'node:fs';
import { platform } from 'node:os';
import { join, resolve } from 'node:path';

const root = resolve(__dirname, '..');

if (platform() !== 'win32') {
  console.log('Advanced overlay is Windows-only; skipping native build.');
  process.exit(0);
}

const source = join(root, 'native', 'advanced-overlay');
const build = join(root, 'build', 'native-overlay');
const output = join(root, 'dist', 'native');
const licenses = join(output, 'licenses');

function run(command: string, args: string[]) {
  const result = spawnSync(command, args, {
    cwd: root,
    stdio: 'inherit',
    shell: false,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} exited with code ${result.status ?? 'unknown'}`);
  }
}

run('cmake', ['-S', source, '-B', build, '-A', 'x64']);
run('cmake', ['--build', build, '--config', 'Release', '--target', 'AkagiAdvancedOverlay']);

const executable = join(build, 'Release', 'AkagiAdvancedOverlay.exe');
if (!existsSync(executable)) {
  throw new Error(`Native overlay build completed without output: ${executable}`);
}

mkdirSync(output, { recursive: true });
copyFileSync(executable, join(output, 'AkagiAdvancedOverlay.exe'));
mkdirSync(licenses, { recursive: true });
copyFileSync(join(source, 'THIRD_PARTY_NOTICES.md'), join(licenses, 'THIRD_PARTY_NOTICES.md'));
copyFileSync(join(source, 'REFERENCE_LICENSE.txt'), join(licenses, 'discord-overlay-example.txt'));
copyFileSync(join(build, '_deps', 'imgui-src', 'LICENSE.txt'), join(licenses, 'dear-imgui.txt'));
copyFileSync(
  join(build, '_deps', 'nlohmann_json-src', 'LICENSE.MIT'),
  join(licenses, 'nlohmann-json.txt'),
);
console.log(`Native overlay copied to ${join(output, 'AkagiAdvancedOverlay.exe')}`);
