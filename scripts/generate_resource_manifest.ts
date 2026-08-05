import {
  createHash,
  createPrivateKey,
  createPublicKey,
  generateKeyPairSync,
  type KeyObject,
  sign,
} from 'node:crypto';
import { createReadStream, existsSync, readFileSync } from 'node:fs';
import { lstat, mkdir, readdir, readFile, realpath, writeFile } from 'node:fs/promises';
import { basename, extname, isAbsolute, join, relative, resolve, sep } from 'node:path';

const rootDir = resolve(__dirname, '..');
const trustAnchorPath = join(rootDir, 'dist', 'main', 'resource-trust-anchor.js');
const trustAnchorPlaceholder = 'AKAGI_RESOURCE_PUBLIC_KEY_PLACEHOLDER';
const pythonCodeExtensions = new Set(['.py', '.pyc']);
const pythonArchiveExtensions = new Set(['.zip', '.egg', '.whl']);
const pluginDataExtensions = new Set(['.yaml', '.yml', '.json']);
const nativeLibraryExtensions = new Set(['.dll', '.pyd', '.so', '.dylib']);
const modelExtensions = new Set(['.pt', '.onnx']);
const hashConcurrency = 8;
const maxManifestEntries = 25_000;
const requiredModels = ['models/mortal.pth', 'models/mortal3p.pth'] as const;

type ProtectedResourceType =
  | 'executable'
  | 'native-library'
  | 'model'
  | 'python-code'
  | 'plugin-data';

interface ManifestEntry {
  path: string;
  type: ProtectedResourceType;
  size: number;
  sha256: string;
}

interface ScanRoot {
  source: string;
  target: string;
}

function normalizePath(value: string): string {
  return value.split(sep).join('/');
}

function isWithin(path: string, root: string): boolean {
  const comparablePath = process.platform === 'win32' ? path.toLowerCase() : path;
  const comparableRoot = process.platform === 'win32' ? root.toLowerCase() : root;
  return comparablePath === comparableRoot || comparablePath.startsWith(`${comparableRoot}/`);
}

function classifyProtectedPath(
  path: string,
  platform: NodeJS.Platform = process.platform,
): ProtectedResourceType | undefined {
  const extension = extname(path).toLowerCase();
  // The macOS manifest is generated before codesign so that the manifest itself is
  // covered by the application bundle signature. Codesign subsequently rewrites
  // Mach-O executables/libraries, therefore their integrity is delegated to the
  // bundle signature instead of recording hashes that would immediately go stale.
  if (platform !== 'darwin') {
    if (extension === '.exe' || (extension === '' && basename(path) === 'akagi-ng')) {
      return 'executable';
    }
    if (nativeLibraryExtensions.has(extension) || basename(path).toLowerCase().includes('.so.')) {
      return 'native-library';
    }
  }
  if (modelExtensions.has(extension)) return 'model';
  if (extension === '.pth') {
    return isWithin(path, 'bin/app_packages') || isWithin(path, 'bin/python')
      ? 'python-code'
      : 'model';
  }
  if (
    (isWithin(path, 'bin/app_packages') || isWithin(path, 'bin/python')) &&
    (pythonCodeExtensions.has(extension) || pythonArchiveExtensions.has(extension))
  ) {
    return 'python-code';
  }
  if (isWithin(path, 'assets/plugins') && pluginDataExtensions.has(extension)) {
    return 'plugin-data';
  }
  return undefined;
}

async function sha256File(path: string): Promise<string> {
  const hash = createHash('sha256');
  await new Promise<void>((resolvePromise, rejectPromise) => {
    const stream = createReadStream(path);
    stream.on('data', (chunk) => hash.update(chunk));
    stream.on('error', rejectPromise);
    stream.on('end', resolvePromise);
  });
  return hash.digest('hex');
}

async function scanRoot(scanRoot: ScanRoot): Promise<ManifestEntry[]> {
  if (!existsSync(scanRoot.source)) return [];
  const candidates: Array<{
    sourcePath: string;
    path: string;
    type: ProtectedResourceType;
    size: number;
  }> = [];

  async function visit(directory: string): Promise<void> {
    const children = await readdir(directory, { withFileTypes: true });
    children.sort((left, right) => left.name.localeCompare(right.name, 'en'));
    for (const child of children) {
      const path = join(directory, child.name);
      const targetPath = normalizePath(join(scanRoot.target, relative(scanRoot.source, path)));
      const type = classifyProtectedPath(targetPath);
      const strictTree =
        isWithin(targetPath, 'bin/app_packages') ||
        isWithin(targetPath, 'bin/python') ||
        isWithin(targetPath, 'assets/plugins');
      if (child.isSymbolicLink()) {
        if (strictTree || type) {
          throw new Error(`Refusing symlink in protected resource tree: ${path}`);
        }
        continue;
      }
      if (child.isDirectory()) {
        await visit(path);
      } else if (child.isFile()) {
        if (!type) continue;
        const stats = await lstat(path);
        candidates.push({
          sourcePath: path,
          path: targetPath,
          type,
          size: stats.size,
        });
      }
    }
  }

  await visit(scanRoot.source);
  const entries = new Array<ManifestEntry>(candidates.length);
  let nextIndex = 0;
  const workers = Array.from(
    { length: Math.min(hashConcurrency, Math.max(candidates.length, 1)) },
    async () => {
      while (nextIndex < candidates.length) {
        const index = nextIndex++;
        const candidate = candidates[index];
        entries[index] = {
          path: candidate.path,
          type: candidate.type,
          size: candidate.size,
          sha256: await sha256File(candidate.sourcePath),
        };
      }
    },
  );
  await Promise.all(workers);
  return entries;
}

export function loadSigningKey(): { privateKey: KeyObject; ephemeral: boolean } {
  const configured = process.env.AKAGI_RESOURCE_SIGNING_KEY?.trim();
  if (!configured) {
    if (process.env.AKAGI_REQUIRE_RESOURCE_SIGNING === '1') {
      throw new Error(
        'AKAGI_RESOURCE_SIGNING_KEY is required when AKAGI_REQUIRE_RESOURCE_SIGNING=1.',
      );
    }
    const { privateKey } = generateKeyPairSync('ed25519');
    return { privateKey, ephemeral: true };
  }

  let keyMaterial: string;
  if (configured.includes('-----BEGIN')) {
    keyMaterial = configured;
  } else if (existsSync(configured)) {
    keyMaterial = readFileSync(configured, 'utf8');
  } else {
    keyMaterial = Buffer.from(configured, 'base64').toString('utf8');
  }
  const privateKey = createPrivateKey(keyMaterial);
  if (privateKey.asymmetricKeyType !== 'ed25519') {
    throw new Error('AKAGI_RESOURCE_SIGNING_KEY must contain an Ed25519 private key.');
  }
  return { privateKey, ephemeral: false };
}

export async function injectTrustAnchor(publicKeySpkiBase64: string): Promise<void> {
  const source = await readFile(trustAnchorPath, 'utf8');
  const firstPlaceholder = source.indexOf(trustAnchorPlaceholder);
  let updated: string;
  if (firstPlaceholder >= 0 && source.indexOf(trustAnchorPlaceholder, firstPlaceholder + 1) < 0) {
    updated = source.replace(trustAnchorPlaceholder, publicKeySpkiBase64);
  } else {
    const assignment = /(exports\.RESOURCE_PUBLIC_KEY_SPKI_BASE64\s*=\s*')[^']+(';)/gu;
    const matches = [...source.matchAll(assignment)];
    if (matches.length !== 1) {
      throw new Error('Compiled resource trust-anchor assignment is missing or ambiguous.');
    }
    updated = source.replace(assignment, `$1${publicKeySpkiBase64}$2`);
  }
  await writeFile(trustAnchorPath, updated, 'utf8');
}

function assertCoverage(entries: ManifestEntry[]): void {
  if (entries.length > maxManifestEntries) {
    throw new Error(`Protected release resources exceed the ${maxManifestEntries} entry limit.`);
  }
  const paths = new Set(entries.map((entry) => entry.path));
  const required = [
    'bin/app_packages/akagi_ng/application.py',
    'bin/app_packages/akagi_ng/__main__.py',
    'assets/plugins/majsoul-max/max_data.yaml',
    ...requiredModels,
  ];
  if (process.platform !== 'darwin') {
    const backend =
      process.platform === 'win32' ? 'bin/python/akagi-ng.exe' : 'bin/python/bin/akagi-ng';
    const nativeExt = process.platform === 'win32' ? 'pyd' : 'so';
    required.push(backend, `lib/libriichi.${nativeExt}`, `lib/libriichi3p.${nativeExt}`);
  }
  if (process.platform === 'win32') {
    required.push('bin/python/python312.dll', 'bin/python/Lib/os.py');
  } else if (process.platform === 'darwin') {
    required.push('bin/python/lib/python3.12/os.py');
  } else {
    required.push('bin/python/lib/libpython3.12.so.1.0', 'bin/python/lib/python3.12/os.py');
  }
  if (process.platform === 'win32') {
    required.push(
      'assets/mihomo/windows-x64/mihomo.exe',
      'assets/privileged/AkagiNg.TunHelper.exe',
    );
  }
  const missing = required.filter((path) => !paths.has(path));
  if (missing.length > 0)
    throw new Error(`Protected release resources are missing: ${missing.join(', ')}`);
  if (!entries.some((entry) => entry.type === 'model')) {
    throw new Error('Protected release resources contain no model files.');
  }
  if (!entries.some((entry) => entry.type === 'python-code')) {
    throw new Error('Protected release resources contain no Python runtime code.');
  }
  if (!entries.some((entry) => entry.type === 'plugin-data')) {
    throw new Error('Protected release resources contain no plugin data.');
  }
}

async function assertUnhashedPlatformResources(packagedRoot: string): Promise<void> {
  if (process.platform !== 'darwin') return;

  // These Mach-O files are rewritten after afterPack by codesign. Their bytes are
  // therefore sealed by the outer .app signature instead of this pre-sign manifest,
  // but a release must still fail closed if a critical runtime file is absent.
  const required = [
    'bin/python/bin/akagi-ng',
    'bin/python/lib/libpython3.12.dylib',
    'lib/libriichi.so',
    'lib/libriichi3p.so',
  ];
  const rootRealPath = await realpath(packagedRoot);
  for (const path of required) {
    const filePath = resolve(packagedRoot, ...path.split('/'));
    const fileRealPath = await realpath(filePath);
    const child = relative(rootRealPath, fileRealPath);
    if (child === '' || child === '..' || child.startsWith(`..${sep}`) || isAbsolute(child)) {
      throw new Error(`Required macOS runtime resolves outside the application root: ${path}`);
    }
    const stats = await lstat(filePath);
    if (!stats.isFile() || stats.isSymbolicLink()) {
      throw new Error(`Required macOS runtime is not a regular file: ${path}`);
    }
  }
}

export async function generatePackagedManifest(
  packagedRoot: string,
  privateKey: KeyObject,
): Promise<void> {
  const packageJson = JSON.parse(await readFile(join(rootDir, 'package.json'), 'utf8')) as {
    version?: unknown;
  };
  if (typeof packageJson.version !== 'string') throw new Error('Root package version is missing.');

  await assertUnhashedPlatformResources(packagedRoot);
  const entries = await scanRoot({ source: packagedRoot, target: '' });
  entries.sort((left, right) => left.path.localeCompare(right.path, 'en'));
  const seen = new Set<string>();
  for (const entry of entries) {
    const identity = process.platform === 'win32' ? entry.path.toLowerCase() : entry.path;
    if (seen.has(identity)) throw new Error(`Duplicate protected release path: ${entry.path}`);
    seen.add(identity);
  }
  assertCoverage(entries);

  const manifest = {
    schemaVersion: 1,
    product: {
      name: 'Akagi-NG',
      version: packageJson.version,
      platform: process.platform,
      architecture: process.arch,
      pythonVersion: '3.12.13',
    },
    entries,
  } as const;
  const manifestBytes = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
  const publicKeyDer = createPublicKey(privateKey).export({ format: 'der', type: 'spki' });
  const signature = {
    schemaVersion: 1,
    algorithm: 'Ed25519',
    keyId: createHash('sha256').update(publicKeyDer).digest('hex'),
    signature: sign(null, manifestBytes, privateKey).toString('base64'),
  } as const;

  // macOS reserves the Contents root for bundle structure and code. Custom
  // metadata belongs in Resources so codesign seals it as data instead of
  // attempting to validate the detached signature as nested executable code.
  const manifestRoot =
    process.platform === 'darwin' ? join(packagedRoot, 'Resources') : packagedRoot;
  await mkdir(manifestRoot, { recursive: true });
  await Promise.all([
    writeFile(join(manifestRoot, 'resource-manifest.json'), manifestBytes),
    writeFile(
      join(manifestRoot, 'resource-manifest.sig'),
      `${JSON.stringify(signature, null, 2)}\n`,
      'utf8',
    ),
  ]);
  console.log(
    `✅ Signed ${entries.length} protected resources (${entries.reduce((sum, entry) => sum + entry.size, 0)} bytes).`,
  );
}

async function main(): Promise<void> {
  const marker = process.argv.indexOf('--packaged-root');
  const packagedRootArgument = marker >= 0 ? process.argv[marker + 1] : undefined;
  const effectiveKey = process.env.AKAGI_RESOURCE_EFFECTIVE_SIGNING_KEY;
  if (!packagedRootArgument) throw new Error('--packaged-root is required.');
  if (!effectiveKey) throw new Error('AKAGI_RESOURCE_EFFECTIVE_SIGNING_KEY is required.');
  const privateKey = createPrivateKey(effectiveKey);
  if (privateKey.asymmetricKeyType !== 'ed25519') {
    throw new Error('The effective resource signing key is not Ed25519.');
  }
  await generatePackagedManifest(resolve(packagedRootArgument), privateKey);
}

if (require.main === module) {
  void main().catch((error: unknown) => {
    console.error('❌ Resource manifest generation failed:', error);
    process.exitCode = 1;
  });
}
