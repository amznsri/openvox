# Homebrew packaging

This directory holds the source-of-truth for the OpenVox Homebrew formula
used by macOS and Linux users who install with:

```bash
brew tap amznsri/openvox
brew install openvox
```

## Files

| File | Purpose |
|---|---|
| [`openvox.rb`](./openvox.rb) | The formula, scaffolded. Resource blocks are auto-generated on release. |

## How publishes work (Phase 4 PR-5)

The release pipeline at `.github/workflows/release.yml` runs on every
tag push. For Homebrew specifically, it:

1. Downloads the PyPI sdist for the new version and computes its
   sha256.
2. Runs `homebrew-pypi-poet` to expand `openvox-core`'s transitive
   Python deps into `resource` blocks with their own sha256s.
3. Patches `url`, `sha256`, `version`, and the resource list inside
   `openvox.rb`.
4. Opens a PR (or pushes directly to `main`) on the separate tap
   repo `amznsri/homebrew-openvox`.

The tap repo only ever has the formula file; it doesn't host any
binaries. Homebrew clients fetch the source tarball from PyPI directly,
so the tap is single-maintainer-light.

## Local testing before release

```bash
# Validate the formula syntactically without installing.
brew install --build-from-source --formula ./openvox.rb

# After install, sanity:
openvox version
openvox --help
```

If `--build-from-source` fails on resource sha256 mismatch, regenerate
the resources block (see header comment in `openvox.rb`).

## Why a separate tap (not Homebrew core)

Homebrew core's review queue is multi-week and gates on broad reverse-
dependencies. A self-maintained tap (`amznsri/homebrew-openvox`) lets
us ship fixes the same day, with the trade-off that users have to
`brew tap` once before `brew install`. Documented as decision #2 in
`docs/PLANNING_SESSION15.md`. Submission to homebrew-core is on the
roadmap once OpenVox has enough community traction to justify the
review queue.
