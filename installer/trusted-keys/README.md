# Trusted release-signing keys

This directory holds the **public** keys OpenAVC trusts to verify release
artifacts. It ships inside every release tarball, so an installed copy carries
the current key set at `$APP_DIR/installer/trusted-keys/` (kept root-owned so the
service user cannot swap in its own key).

Verifiers accept an artifact if **any** `*.pem` here validates its detached
`.sig`. The set is consulted by:

- `installer/update-helper.sh` (root, before extracting a self-update tarball) —
  the authoritative gate that closes the service-user-to-root escalation.
- `installer/openavc-macos-run.sh` (root, macOS app swap).
- `installer/install.sh` (verifies `SHA256SUMS.txt.sig` before trusting the
  checksums).
- `server/updater/manager.py` (defense-in-depth pre-check on download).

## Arming state (why an empty dir is safe to ship)

**No `*.pem` present = signing not yet armed.** Verifiers log a warning and
proceed (they do not refuse), so shipping this code before the production key
exists cannot brick auto-update. The moment a production key is committed here
and releases are signed, verification is enforced fail-closed: a present key
with a missing or invalid `.sig` is refused.

## Production key ceremony (one-time, before the first signed release)

```sh
# Generate the keypair (keep the .key OFFLINE / in a password manager)
openssl ecparam -genkey -name prime256v1 -noout -out release-signing.key
openssl ec -in release-signing.key -pubout -out openavc-release.pem

# 1. Commit openavc-release.pem into this directory.
# 2. Store the base64 of release-signing.key as the RELEASE_SIGNING_KEY
#    GitHub Actions secret (arms the "Sign release artifacts" step):
#       base64 -w0 release-signing.key   # (macOS: base64 -i release-signing.key)
# 3. Cut a release — its assets are now signed and verification is enforced.
```

## Rotation (no install is ever pinned to a single key)

1. Add the new public key here in a release **signed by the old key** (deployed
   installs now trust both).
2. A later release: switch `RELEASE_SIGNING_KEY` to the new private key.
3. A release after that: remove the old public key.

Test keys used for on-device validation are throwaway and never committed here.
