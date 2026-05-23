# WinGet packaging

Source-of-truth for the OpenVox WinGet manifests. After a release,
these files (with version + SHA256 substituted) get PR'd into the
[microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs)
catalogue.

End-user command after the PR is accepted:

```powershell
winget install OpenVox.OpenVox
```

## Files

| File | Role |
|---|---|
| `OpenVox.OpenVox.yaml`              | Version manifest (top-level pointer) |
| `OpenVox.OpenVox.installer.yaml`    | Where to download + how to extract |
| `OpenVox.OpenVox.locale.en-US.yaml` | English listing-page text |

## How publishes work (Phase 4 PR-5)

The release pipeline at `.github/workflows/release.yml`:

1. Builds the wheel + uploads to GitHub Releases.
2. Computes the SHA256 of the wheel.
3. Substitutes `0.0.0` → real version and the placeholder SHA into
   all three manifests.
4. Drops them into `manifests/o/OpenVox/OpenVox/<version>/` in a
   `microsoft/winget-pkgs` fork.
5. Opens a PR via [wingetcreate](https://github.com/microsoft/winget-create).

## Why we ship the wheel via the `zip` InstallerType

`winget` supports installing arbitrary executables out of a zip
archive (via `NestedInstallerType: portable`). Python wheels ARE zip
files, so we point winget at the bin shim (`Scripts\openvox.exe`)
that pip lays down inside the wheel.

The alternative — building a `.msi` per release — would require a
code-signing certificate (~$300/yr for a non-EV cert; $400+/yr for
EV) and a Windows-only build runner. PLANNING_SESSION15.md §Phase 4
explicitly defers signed installers; the wheel-via-zip path costs
$0 and ships day-one.

## Validation before submitting a PR

Microsoft ships `winget validate` for syntactic checks:

```powershell
winget validate --manifest packaging/winget/
```

For end-to-end pre-merge testing on a clean Windows VM:

```powershell
winget install --manifest packaging/winget/
```
