import { spawnSync } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';

const rootDir = resolve(__dirname, '..');
const sourceDir = join(rootDir, 'electron', 'privileged-helper');
const outputDir = join(rootDir, 'build', 'privileged');
const outputPath = join(outputDir, 'AkagiNg.TunHelper.exe');

function findCompiler(): string | null {
  const frameworkRoot = process.env.WINDIR ?? 'C:\\Windows';
  const candidates = [
    join(frameworkRoot, 'Microsoft.NET', 'Framework64', 'v4.0.30319', 'csc.exe'),
    join(frameworkRoot, 'Microsoft.NET', 'Framework', 'v4.0.30319', 'csc.exe'),
  ];
  return candidates.find((candidate) => existsSync(candidate)) ?? null;
}

if (process.platform !== 'win32') {
  console.log('ℹ️ Skipping the Windows TUN helper build on this platform.');
  process.exit(0);
}

const compiler = findCompiler();
if (!compiler) {
  throw new Error(
    'The .NET Framework C# compiler required for the Windows TUN helper was not found.',
  );
}

mkdirSync(outputDir, { recursive: true });
const result = spawnSync(
  compiler,
  [
    '/nologo',
    '/target:winexe',
    '/platform:x64',
    '/optimize+',
    '/reference:System.Web.Extensions.dll',
    `/win32manifest:${join(sourceDir, 'AkagiNg.TunHelper.manifest')}`,
    `/out:${outputPath}`,
    join(sourceDir, 'TunHelper.cs'),
  ],
  { cwd: rootDir, encoding: 'utf8', windowsHide: true },
);

if (result.status !== 0 || !existsSync(outputPath)) {
  throw new Error(`Windows TUN helper build failed:\n${result.stdout}\n${result.stderr}`);
}

copyFileSync(
  join(sourceDir, 'AkagiNg.TunHelper.manifest'),
  join(outputDir, 'AkagiNg.TunHelper.manifest'),
);
console.log(`✅ Built least-privilege TUN helper: ${outputPath}`);
