import {
  createHash,
  createPrivateKey,
  createPublicKey,
  generateKeyPairSync,
  type KeyObject,
  sign,
} from 'node:crypto';
import { createReadStream, existsSync, readFileSync } from 'node:fs';
import { lstat, mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
import { basename, extname, join, relative, resolve, sep } from 'node:path';

const rootDir = resolve(__dirname, '..');
const trustAnchorPath = join(rootDir, 'dist', 'main', 'resource-trust-anchor.js');
const trustAnchorPlaceholder = 'AKAGI_RESOURCE_PUBLIC_KEY_PLACEHOLDER';
const protectedExtensions = new Set(['.exe', '.pyd', '.so', '.dylib', '.pth', '.pt', '.onnx']);

interface ManifestEntry {
  path: string;
  type: 'executable' | 'native-library' | 'model';
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

function classify(path: string): ManifestEntry['type'] {
  const extension = extname(path).toLowerCase();
  if (['.pth', '.pt', '.onnx'].includes(extension)) return 'model';
  if (extension === '.exe' || (extension === '' && basename(path) === 'akagi-ng')) {
    return 'executable';
  }
  return 'native-library';
}

function isProtected(path: string): boolean {
  return protectedExtensions.has(extname(path).toLowerCase()) || basename(path) === 'akagi-ng';
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
  const entries: ManifestEntry[] = [];

  async function visit(directory: string): Promise<void> {
    const children = await readdir(directory, { withFileTypes: true });
    children.sort((left, right) => left.name.localeCompare(right.name, 'en'));
    for (const child of children) {
      const path = join(directory, child.name);
      if (child.isSymbolicLink())
        throw new Error(`Refusing symlink in protected resource tree: ${path}`);
      if (child.isDirectory()) {
        await visit(path);
      } else if (child.isFile() && isProtected(path)) {
        const stats = await lstat(path);
        const targetPath = normalizePath(join(scanRoot.target, relative(scanRoot.source, path)));
        entries.push({
          path: targetPath,
          type: classify(path),
          size: stats.size,
          sha256: await sha256File(path),
        });
      }
    }
  }

  await visit(scanRoot.source);
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
  const paths = new Set(entries.map((entry) => entry.path));
  const backend =
    process.platform === 'win32' ? 'bin/python/akagi-ng.exe' : 'bin/python/bin/akagi-ng';
  const nativeExt = process.platform === 'win32' ? 'pyd' : 'so';
  const required = [backend, `lib/libriichi.${nativeExt}`, `lib/libriichi3p.${nativeExt}`];
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
}

export async function generatePackagedManifest(
  packagedRoot: string,
  privateKey: KeyObject,
): Promise<void> {
  const packageJson = JSON.parse(await readFile(join(rootDir, 'package.json'), 'utf8')) as {
    version?: unknown;
  };
  if (typeof packageJson.version !== 'string') throw new Error('Root package version is missing.');

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

  await mkdir(packagedRoot, { recursive: true });
  await Promise.all([
    writeFile(join(packagedRoot, 'resource-manifest.json'), manifestBytes),
    writeFile(
      join(packagedRoot, 'resource-manifest.sig'),
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
