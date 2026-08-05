import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

type JsonObject = Record<string, unknown>;

const checkOnly = process.argv.includes('--check');

function readJson(path: string): JsonObject {
  return JSON.parse(readFileSync(path, 'utf8')) as JsonObject;
}

function writeOrCheck(path: string, nextContent: string): void {
  const currentContent = readFileSync(path, 'utf8');
  if (currentContent === nextContent) {
    console.log(`Already synchronized: ${path}`);
    return;
  }

  if (checkOnly) {
    throw new Error(`Version drift detected in ${path}. Run npm run sync-version.`);
  }

  writeFileSync(path, nextContent);
  console.log(`Synchronized: ${path}`);
}

function stringifyJson(value: JsonObject): string {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function requireObject(value: unknown, label: string): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} is missing or is not an object`);
  }
  return value as JsonObject;
}

/**
 * Synchronizes the root package.json version to all packaged runtimes and the
 * npm lockfile. Pass --check to verify the repository without writing files.
 */
function syncVersion(): void {
  try {
    const projectRoot = resolve(__dirname, '..');
    const rootPackageJsonPath = join(projectRoot, 'package.json');

    if (!existsSync(rootPackageJsonPath)) {
      throw new Error(`Master package.json not found at ${rootPackageJsonPath}`);
    }

    const rootPackageJson = readJson(rootPackageJsonPath);
    const version = rootPackageJson.version;
    if (typeof version !== 'string' || version.trim() === '') {
      throw new Error('Version missing in root package.json');
    }
    console.log(`Master version: ${version}`);

    const pyprojectPath = join(projectRoot, 'akagi_backend', 'pyproject.toml');
    const pyprojectContent = readFileSync(pyprojectPath, 'utf8');
    const pyprojectVersionPattern = /^(\s*version\s*=\s*)["']([^"']+)["']/m;
    if (!pyprojectVersionPattern.test(pyprojectContent)) {
      throw new Error(`Version field not found in ${pyprojectPath}`);
    }
    writeOrCheck(
      pyprojectPath,
      pyprojectContent.replace(pyprojectVersionPattern, `$1"${version}"`),
    );

    const workspacePackagePaths = [
      join(projectRoot, 'akagi_frontend', 'package.json'),
      join(projectRoot, 'electron', 'package.json'),
    ];
    for (const packagePath of workspacePackagePaths) {
      const packageJson = readJson(packagePath);
      packageJson.version = version;
      if ('_comment' in packageJson) delete packageJson._comment;
      writeOrCheck(packagePath, stringifyJson(packageJson));
    }

    const packageLockPath = join(projectRoot, 'package-lock.json');
    const packageLock = readJson(packageLockPath);
    const lockPackages = requireObject(packageLock.packages, 'package-lock.json packages');
    packageLock.version = version;
    for (const packagePath of ['', 'akagi_frontend', 'electron']) {
      const lockPackage = requireObject(
        lockPackages[packagePath],
        `package-lock.json packages[${JSON.stringify(packagePath)}]`,
      );
      lockPackage.version = version;
    }
    writeOrCheck(packageLockPath, stringifyJson(packageLock));

    console.log(
      checkOnly
        ? 'Monorepo versions are synchronized.'
        : 'Monorepo versions synchronized successfully.',
    );
  } catch (error) {
    console.error('Failed to synchronize versions:', error);
    process.exitCode = 1;
  }
}

syncVersion();
