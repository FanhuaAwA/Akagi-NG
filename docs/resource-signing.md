# Resource manifest signing

Packaged builds create `resource-manifest.json` and `resource-manifest.sig` next
to the application resources. The manifest records the product version,
platform, architecture, Python version, size, and SHA-256 of every packaged
executable, native runtime library, model, importable Python source/archive, and
built-in plugin data file. The detached signature uses Ed25519, and its public
key is injected into the compiled Electron main process immediately before
packaging.

The protected loose-file scopes are deliberately explicit:

- `bin/app_packages`: `.py`, `.pyc`, `.pth`, `.zip`, `.egg`, `.whl`, executables,
  and native libraries;
- `bin/python`: Python source/bytecode/import archives, the interpreter, and
  native libraries (including versioned `.so.*` files);
- `assets/plugins`: `.yaml`, `.yml`, and `.json` plugin data;
- packaged executables, native libraries, and model files elsewhere in the
  release tree.

Validation rejects unsupported path/type combinations, duplicate paths,
symlinks in Python/plugin trees, and protected files that were added without a
signed manifest entry. Release builds retain precompiled `.pyc` files for
startup performance and authenticate both source and bytecode; the packaged
backend disables bytecode writes so those signed caches cannot be rewritten and
new unlisted caches are not created. Runtime-owned `config/**` (including plugin
settings), `logs/**`, and other user data are not included because they must
remain writable.

Windows creates the manifest after executable resource editing/signing. Linux
creates it after packing because an unsigned Linux build does not emit an
`afterSign` hook. macOS creates the manifest inside
`Akagi-NG.app/Contents` before codesign so the manifest and signature are sealed
with the bundle. Since codesign rewrites Mach-O bytes afterward, macOS delegates
executable/native-library integrity to the bundle signature while the resource
manifest independently authenticates Python code, models, and built-in plugin
data.

The current public workflow has no Windows Authenticode or Apple Developer ID
and notarization credentials. Its macOS seal may therefore be ad-hoc: useful for
bundle consistency, but not proof of publisher identity. Release notes must
disclose that limitation until trusted platform credentials are configured.

This manifest only authenticates the loose resources listed above relative to
the public key embedded in Electron's main-process code. It does **not** protect
`app.asar`, the embedded trust anchor itself, or the Electron executable from an
attacker who can replace the installed application. It is not a substitute for
platform code signing (Authenticode, Apple code signing/notarization, or the
equivalent package signature) and a trusted update channel. Do not describe a
build as OS-signed unless that platform's signing step was actually configured
and verified.

For an official release, store an Ed25519 PKCS#8 private key in the GitHub
Actions secret `AKAGI_RESOURCE_SIGNING_KEY`. The value can be PEM text or the
Base64 encoding of PEM text. The release workflow sets
`AKAGI_REQUIRE_RESOURCE_SIGNING=1` and fails closed if the secret is missing or
invalid. A key can be generated offline with:

```powershell
openssl genpkey -algorithm Ed25519 -out akagi-resource-signing.pem
```

Keep the private key outside the repository. Ordinary local and branch builds
use a one-time in-memory key when the secret is absent; this supports tamper
testing but does not provide a stable release provenance identity. A stable
resource-signing key provides provenance only while the application code and
embedded trust anchor are themselves delivered through a trusted boundary.
