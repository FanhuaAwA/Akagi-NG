import { createHash, createPublicKey, verify } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { lstat, readdir, readFile, realpath } from 'node:fs/promises';
import { basename, extname, isAbsolute, join, relative, resolve, sep } from 'node:path';

import { RESOURCE_PUBLIC_KEY_SPKI_BASE64 } from './resource-trust-anchor.js';

const MANIFEST_FILE = 'resource-manifest.json';
const SIGNATURE_FILE = 'resource-manifest.sig';
const SHA256_PATTERN = /^[a-f0-9]{64}$/u;
const PYTHON_CODE_EXTENSIONS = new Set(['.py', '.pyc']);
const PYTHON_ARCHIVE_EXTENSIONS = new Set(['.zip', '.egg', '.whl']);
const PLUGIN_DATA_EXTENSIONS = new Set(['.yaml', '.yml', '.json']);
const NATIVE_LIBRARY_EXTENSIONS = new Set(['.dll', '.pyd', '.so', '.dylib']);
const MODEL_EXTENSIONS = new Set(['.pt', '.onnx']);
const MAX_MANIFEST_ENTRIES = 25_000;
const MAX_MANIFEST_BYTES = 16 * 1024 * 1024;
const MAX_SIGNATURE_BYTES = 4 * 1024;
const HASH_CONCURRENCY = 8;
const REQUIRED_MODELS = ['models/mortal.pth', 'models/mortal3p.pth'] as const;

type ProtectedResourceType =
  | 'executable'
  | 'native-library'
  | 'model'
  | 'python-code'
  | 'plugin-data';

export type IntegrityState = 'valid' | 'invalid' | 'not-required';

export interface ResourceStatus {
  lib: boolean;
  models: boolean;
  integrity: IntegrityState;
  manifestVersion?: number;
  verifiedFiles: number;
  verifiedBytes: number;
  missingCritical: string[];
  missingOptional: string[];
  errors: string[];
}

interface ResourceManifestEntry {
  path: string;
  type: ProtectedResourceType;
  size: number;
  sha256: string;
}

interface ResourceManifest {
  schemaVersion: 1;
  product: {
    name: 'Akagi-NG';
    version: string;
    platform: NodeJS.Platform;
    architecture: string;
    pythonVersion: string;
  };
  entries: ResourceManifestEntry[];
}

interface ResourceSignature {
  schemaVersion: 1;
  algorithm: 'Ed25519';
  keyId: string;
  signature: string;
}

export interface ResourceValidatorOptions {
  enforceIntegrity?: boolean;
  expectedVersion?: string;
  platform?: NodeJS.Platform;
  architecture?: string;
  trustedPublicKeySpkiBase64?: string;
}

function isWithin(path: string, root: string, platform: NodeJS.Platform): boolean {
  const comparablePath = platform === 'win32' ? path.toLowerCase() : path;
  const comparableRoot = platform === 'win32' ? root.toLowerCase() : root;
  return comparablePath === comparableRoot || comparablePath.startsWith(`${comparableRoot}/`);
}

function classifyProtectedPath(
  path: string,
  platform: NodeJS.Platform,
): ProtectedResourceType | undefined {
  const extension = extname(path).toLowerCase();
  // On macOS this manifest is created before codesign and is itself sealed by the
  // app-bundle signature. Codesign rewrites Mach-O files afterward, so executable
  // and native-library integrity is intentionally delegated to that outer seal.
  if (platform !== 'darwin') {
    if (extension === '.exe' || (extension === '' && basename(path) === 'akagi-ng')) {
      return 'executable';
    }
    if (NATIVE_LIBRARY_EXTENSIONS.has(extension) || basename(path).toLowerCase().includes('.so.')) {
      return 'native-library';
    }
  }
  if (MODEL_EXTENSIONS.has(extension)) return 'model';
  if (extension === '.pth') {
    return isWithin(path, 'bin/app_packages', platform) || isWithin(path, 'bin/python', platform)
      ? 'python-code'
      : 'model';
  }
  if (
    (isWithin(path, 'bin/app_packages', platform) || isWithin(path, 'bin/python', platform)) &&
    (PYTHON_CODE_EXTENSIONS.has(extension) || PYTHON_ARCHIVE_EXTENSIONS.has(extension))
  ) {
    return 'python-code';
  }
  if (isWithin(path, 'assets/plugins', platform) && PLUGIN_DATA_EXTENSIONS.has(extension)) {
    return 'plugin-data';
  }
  return undefined;
}

export class ResourceValidator {
  private readonly enforceIntegrity: boolean;
  private readonly expectedVersion?: string;
  private readonly platform: NodeJS.Platform;
  private readonly architecture: string;
  private readonly trustedPublicKeySpkiBase64: string;

  constructor(
    private readonly projectRoot: string,
    options: ResourceValidatorOptions = {},
  ) {
    this.enforceIntegrity = options.enforceIntegrity ?? false;
    this.expectedVersion = options.expectedVersion;
    this.platform = options.platform ?? process.platform;
    this.architecture = options.architecture ?? process.arch;
    this.trustedPublicKeySpkiBase64 =
      options.trustedPublicKeySpkiBase64 ?? RESOURCE_PUBLIC_KEY_SPKI_BASE64;
  }

  public async validate(): Promise<ResourceStatus> {
    if (!this.enforceIntegrity) return await this.validateDevelopmentResources();
    try {
      const result = await this.verifySignedManifest();
      return {
        lib: true,
        models: true,
        integrity: 'valid',
        manifestVersion: 1,
        verifiedFiles: result.files,
        verifiedBytes: result.bytes,
        missingCritical: [],
        missingOptional: [],
        errors: [],
      };
    } catch (error) {
      return {
        lib: false,
        models: false,
        integrity: 'invalid',
        verifiedFiles: 0,
        verifiedBytes: 0,
        missingCritical: ['resource-integrity'],
        missingOptional: [],
        errors: [error instanceof Error ? error.message : String(error)],
      };
    }
  }

  private async validateDevelopmentResources(): Promise<ResourceStatus> {
    const [lib, models] = await Promise.all([
      this.hasExpectedLibraries(join(this.projectRoot, 'lib')),
      this.hasModel(join(this.projectRoot, 'models')),
    ]);
    return {
      lib,
      models,
      integrity: 'not-required',
      verifiedFiles: 0,
      verifiedBytes: 0,
      missingCritical: lib ? [] : ['lib'],
      missingOptional: models ? [] : ['models'],
      errors: [],
    };
  }

  private async verifySignedManifest(): Promise<{ files: number; bytes: number }> {
    if (this.trustedPublicKeySpkiBase64 === 'AKAGI_RESOURCE_PUBLIC_KEY_PLACEHOLDER') {
      throw new Error('Resource trust anchor was not injected during packaging.');
    }
    const manifestRoot =
      this.platform === 'darwin' ? join(this.projectRoot, 'Resources') : this.projectRoot;
    const manifestPath = join(manifestRoot, MANIFEST_FILE);
    const signaturePath = join(manifestRoot, SIGNATURE_FILE);
    const [manifestSize, signatureSize] = await Promise.all([
      this.assertRegularFile(manifestPath, 'resource manifest'),
      this.assertRegularFile(signaturePath, 'resource signature'),
    ]);
    if (manifestSize > MAX_MANIFEST_BYTES) {
      throw new Error('Resource manifest exceeds the maximum supported size.');
    }
    if (signatureSize > MAX_SIGNATURE_BYTES) {
      throw new Error('Resource signature exceeds the maximum supported size.');
    }
    const [manifestBytes, signatureText] = await Promise.all([
      readFile(manifestPath),
      readFile(signaturePath, 'utf8'),
    ]);
    const signature = this.parseSignature(signatureText);
    const publicKeyDer = Buffer.from(this.trustedPublicKeySpkiBase64, 'base64');
    if (publicKeyDer.length === 0) throw new Error('Resource trust anchor is empty.');
    const keyId = createHash('sha256').update(publicKeyDer).digest('hex');
    if (signature.keyId !== keyId) {
      throw new Error('Resource manifest signature key does not match the trusted key.');
    }
    const publicKey = createPublicKey({ key: publicKeyDer, format: 'der', type: 'spki' });
    if (publicKey.asymmetricKeyType !== 'ed25519') {
      throw new Error('Resource trust anchor is not an Ed25519 public key.');
    }
    const signatureValue = Buffer.from(signature.signature, 'base64');
    if (signatureValue.length !== 64 || !verify(null, manifestBytes, publicKey, signatureValue)) {
      throw new Error('Resource manifest signature verification failed.');
    }

    const manifest = this.parseManifest(manifestBytes.toString('utf8'));
    this.assertManifestIdentity(manifest);
    this.assertManifestCoverage(manifest);
    const rootRealPath = await realpath(this.projectRoot);
    await this.assertUnhashedPlatformResources(rootRealPath);
    await this.assertProtectedInventory(manifest.entries);
    return await this.verifyManifestEntries(manifest.entries, rootRealPath);
  }

  private parseManifest(value: string): ResourceManifest {
    let parsed: unknown;
    try {
      parsed = JSON.parse(value);
    } catch {
      throw new Error('Resource manifest is not valid JSON.');
    }
    if (!parsed || typeof parsed !== 'object')
      throw new Error('Resource manifest must be an object.');
    const manifest = parsed as Partial<ResourceManifest>;
    if (manifest.schemaVersion !== 1) throw new Error('Unsupported resource manifest version.');
    if (!manifest.product || typeof manifest.product !== 'object') {
      throw new Error('Resource manifest product metadata is missing.');
    }
    if (!Array.isArray(manifest.entries) || manifest.entries.length === 0) {
      throw new Error('Resource manifest contains no protected resources.');
    }
    if (manifest.entries.length > MAX_MANIFEST_ENTRIES)
      throw new Error('Resource manifest contains too many entries.');

    const seen = new Set<string>();
    for (const entry of manifest.entries) {
      if (!entry || typeof entry !== 'object')
        throw new Error('Resource manifest entry is invalid.');
      this.assertSafeRelativePath(entry.path);
      const expectedType = classifyProtectedPath(entry.path, this.platform);
      if (!expectedType) {
        throw new Error(`Unsupported protected resource path or extension: ${entry.path}`);
      }
      if (entry.type !== expectedType) {
        throw new Error(
          `Protected resource type mismatch for ${entry.path}: expected ${expectedType}.`,
        );
      }
      if (!Number.isSafeInteger(entry.size) || entry.size < 0) {
        throw new Error(`Invalid protected resource size: ${entry.path}`);
      }
      if (typeof entry.sha256 !== 'string' || !SHA256_PATTERN.test(entry.sha256)) {
        throw new Error(`Invalid protected resource SHA-256: ${entry.path}`);
      }
      const identity = this.platform === 'win32' ? entry.path.toLowerCase() : entry.path;
      if (seen.has(identity)) throw new Error(`Duplicate protected resource path: ${entry.path}`);
      seen.add(identity);
    }
    return manifest as ResourceManifest;
  }

  private parseSignature(value: string): ResourceSignature {
    let parsed: unknown;
    try {
      parsed = JSON.parse(value);
    } catch {
      throw new Error('Resource signature is not valid JSON.');
    }
    if (!parsed || typeof parsed !== 'object')
      throw new Error('Resource signature must be an object.');
    const signature = parsed as Partial<ResourceSignature>;
    if (
      signature.schemaVersion !== 1 ||
      signature.algorithm !== 'Ed25519' ||
      typeof signature.keyId !== 'string' ||
      !SHA256_PATTERN.test(signature.keyId) ||
      typeof signature.signature !== 'string'
    ) {
      throw new Error('Resource signature metadata is invalid.');
    }
    return signature as ResourceSignature;
  }

  private assertManifestIdentity(manifest: ResourceManifest): void {
    if (manifest.product.name !== 'Akagi-NG')
      throw new Error('Resource manifest product mismatch.');
    if (this.expectedVersion && manifest.product.version !== this.expectedVersion) {
      throw new Error('Resource manifest version does not match the desktop application.');
    }
    if (manifest.product.platform !== this.platform) {
      throw new Error('Resource manifest platform does not match the current platform.');
    }
    if (manifest.product.architecture !== this.architecture) {
      throw new Error('Resource manifest architecture does not match the current architecture.');
    }
    if (!/^3\.12(?:\.|$)/u.test(manifest.product.pythonVersion)) {
      throw new Error('Resource manifest Python runtime version is unsupported.');
    }
  }

  private assertManifestCoverage(manifest: ResourceManifest): void {
    const paths = new Set(
      manifest.entries.map((entry) =>
        this.platform === 'win32' ? entry.path.toLowerCase() : entry.path,
      ),
    );
    const required = [
      'bin/app_packages/akagi_ng/application.py',
      'bin/app_packages/akagi_ng/__main__.py',
      'assets/plugins/majsoul-max/max_data.yaml',
      ...REQUIRED_MODELS,
    ];
    if (this.platform !== 'darwin') {
      const nativeExtension = this.platform === 'win32' ? 'pyd' : 'so';
      const backend =
        this.platform === 'win32' ? 'bin/python/akagi-ng.exe' : 'bin/python/bin/akagi-ng';
      required.push(
        backend,
        `lib/libriichi.${nativeExtension}`,
        `lib/libriichi3p.${nativeExtension}`,
      );
    }
    if (this.platform === 'win32') {
      required.push('bin/python/python312.dll', 'bin/python/Lib/os.py');
    } else if (this.platform === 'darwin') {
      required.push('bin/python/lib/python3.12/os.py');
    } else {
      required.push('bin/python/lib/libpython3.12.so.1.0', 'bin/python/lib/python3.12/os.py');
    }
    if (this.platform === 'win32') {
      required.push(
        'assets/mihomo/windows-x64/mihomo.exe',
        'assets/privileged/AkagiNg.TunHelper.exe',
      );
    }
    const missing = required.filter(
      (path) => !paths.has(this.platform === 'win32' ? path.toLowerCase() : path),
    );
    if (missing.length > 0) {
      throw new Error(`Protected release resources are missing: ${missing.join(', ')}`);
    }
    for (const type of ['model', 'python-code', 'plugin-data'] as const) {
      if (!manifest.entries.some((entry) => entry.type === type)) {
        throw new Error(`Protected release resources contain no ${type} entries.`);
      }
    }
  }

  private async assertUnhashedPlatformResources(rootRealPath: string): Promise<void> {
    if (this.platform !== 'darwin') return;

    for (const path of [
      'bin/python/bin/akagi-ng',
      'bin/python/lib/libpython3.12.dylib',
      'lib/libriichi.so',
      'lib/libriichi3p.so',
    ]) {
      const filePath = resolve(this.projectRoot, ...path.split('/'));
      await this.assertPathContained(rootRealPath, filePath, path);
      const stats = await lstat(filePath);
      if (!stats.isFile() || stats.isSymbolicLink()) {
        throw new Error(`Required macOS runtime is not a regular file: ${path}`);
      }
    }
  }

  private async assertProtectedInventory(entries: ResourceManifestEntry[]): Promise<void> {
    const listedPaths = new Set(
      entries.map((entry) => (this.platform === 'win32' ? entry.path.toLowerCase() : entry.path)),
    );

    const visit = async (directory: string): Promise<void> => {
      const children = await readdir(directory, { withFileTypes: true });
      for (const child of children) {
        const filePath = join(directory, child.name);
        const entryPath = relative(this.projectRoot, filePath).split(sep).join('/');
        const expectedType = classifyProtectedPath(entryPath, this.platform);
        const strictTree =
          isWithin(entryPath, 'bin/app_packages', this.platform) ||
          isWithin(entryPath, 'bin/python', this.platform) ||
          isWithin(entryPath, 'assets/plugins', this.platform);
        if (child.isSymbolicLink()) {
          if (strictTree || expectedType) {
            throw new Error(`Symlink found in protected resource tree: ${entryPath}`);
          }
          continue;
        }
        if (child.isDirectory()) {
          await visit(filePath);
          continue;
        }
        if (!child.isFile() || !expectedType) continue;
        const identity = this.platform === 'win32' ? entryPath.toLowerCase() : entryPath;
        if (!listedPaths.has(identity)) {
          throw new Error(`Protected resource is not listed in the signed manifest: ${entryPath}`);
        }
      }
    };

    await visit(this.projectRoot);
  }

  private async verifyManifestEntries(
    entries: ResourceManifestEntry[],
    rootRealPath: string,
  ): Promise<{ files: number; bytes: number }> {
    let nextIndex = 0;
    const workers = Array.from({ length: Math.min(HASH_CONCURRENCY, entries.length) }, async () => {
      let bytes = 0;
      let files = 0;
      while (nextIndex < entries.length) {
        const entry = entries[nextIndex++];
        const filePath = resolve(this.projectRoot, ...entry.path.split('/'));
        await this.assertPathContained(rootRealPath, filePath, entry.path);
        const stats = await lstat(filePath);
        if (!stats.isFile() || stats.isSymbolicLink()) {
          throw new Error(`Protected resource is not a regular file: ${entry.path}`);
        }
        if (stats.size !== entry.size) {
          throw new Error(`Protected resource size mismatch: ${entry.path}`);
        }
        if ((await this.sha256File(filePath)) !== entry.sha256) {
          throw new Error(`Protected resource SHA-256 mismatch: ${entry.path}`);
        }
        files += 1;
        bytes += stats.size;
      }
      return { files, bytes };
    });
    const results = await Promise.all(workers);
    return results.reduce(
      (total, result) => ({
        files: total.files + result.files,
        bytes: total.bytes + result.bytes,
      }),
      { files: 0, bytes: 0 },
    );
  }

  private assertSafeRelativePath(value: unknown): asserts value is string {
    if (typeof value !== 'string' || value.length === 0 || value.includes('\\')) {
      throw new Error('Protected resource path must be a non-empty normalized POSIX path.');
    }
    if (isAbsolute(value) || value.startsWith('/') || value.includes('\0')) {
      throw new Error(`Protected resource path is absolute or malformed: ${value}`);
    }
    if (value.split('/').some((part) => part.length === 0 || part === '.' || part === '..')) {
      throw new Error(`Protected resource path escapes its root: ${value}`);
    }
  }

  private async assertPathContained(
    rootRealPath: string,
    filePath: string,
    entryPath: string,
  ): Promise<void> {
    const fileRealPath = await realpath(filePath);
    const child = relative(rootRealPath, fileRealPath);
    if (child === '' || child === '..' || child.startsWith(`..${sep}`) || isAbsolute(child)) {
      throw new Error(`Protected resource resolves outside the application root: ${entryPath}`);
    }
  }

  private async assertRegularFile(filePath: string, label: string): Promise<number> {
    const stats = await lstat(filePath);
    if (!stats.isFile() || stats.isSymbolicLink())
      throw new Error(`The ${label} is not a regular file.`);
    return stats.size;
  }

  private async sha256File(filePath: string): Promise<string> {
    const hash = createHash('sha256');
    await new Promise<void>((resolvePromise, rejectPromise) => {
      const stream = createReadStream(filePath);
      stream.on('data', (chunk) => hash.update(chunk));
      stream.on('error', rejectPromise);
      stream.on('end', resolvePromise);
    });
    return hash.digest('hex');
  }

  private async hasExpectedLibraries(dirPath: string): Promise<boolean> {
    try {
      const ext = this.platform === 'win32' ? 'pyd' : 'so';
      const [first, second] = await Promise.all([
        lstat(join(dirPath, `libriichi.${ext}`)),
        lstat(join(dirPath, `libriichi3p.${ext}`)),
      ]);
      return first.isFile() && second.isFile();
    } catch {
      return false;
    }
  }

  private async hasModel(dirPath: string): Promise<boolean> {
    try {
      return (await readdir(dirPath)).some((file) => file.endsWith('.pth'));
    } catch {
      return false;
    }
  }
}
