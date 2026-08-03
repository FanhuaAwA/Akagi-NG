# Resource manifest signing

Packaged builds create `resource-manifest.json` and `resource-manifest.sig` next
to the application resources. The manifest records the product version,
platform, architecture, Python version, size, and SHA-256 of every packaged
executable, native Python extension, and model. The detached signature uses
Ed25519, and its public key is injected into the compiled Electron main process
immediately before packaging.

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
testing but does not provide a stable release provenance identity.
