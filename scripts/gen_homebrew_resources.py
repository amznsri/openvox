"""Generate Homebrew `resource` blocks for openvox-core.

Replaces `homebrew-pypi-poet`, which we'd been using since v0.1.x but has
been broken on this project for two reasons:

1. **Wheel-only deps produce empty sha256s.** Poet expects every dep to
   publish an sdist on PyPI. Several of ours (audioop-lts, some pyobjc
   transitives) ship wheels only — poet silently emits a `resource` block
   with the URL pointing nowhere and an empty `sha256`. The resulting
   formula installs partially then dies, and the user sees a broken
   `brew install openvox`.

2. **Poet itself doesn't run on pip ≥ 24.** It imports `pkg_resources`,
   which newer pip releases no longer auto-install. The workflow's
   `pip install homebrew-pypi-poet` step succeeds but the next `poet …`
   invocation crashes with `ModuleNotFoundError: pkg_resources`.

The replacement is a two-pass artefact picker:

  Pass 1 (`_resolve`)
    pip dry-runs an install of the pinned spec and dumps a JSON report.
    From it we extract the exact (name, version) tree pip would install.
    We *ignore* the URLs pip chose because they're platform-specific
    (pip naturally picks the wheel for the host machine).

  Pass 2 (`_pick_artefact`) — **wheel-first** ordering
    For each (name, version) we query the PyPI JSON API and pick a
    binary artefact in this priority order:

      a. *Universal wheel* (`-py3-none-any.whl` or
         `-py2.py3-none-any.whl`) — pure-Python, one URL works on
         every OS, brew unpacks and we're done. No compilation.

      b. *Per-OS wheels* — compiled deps like numpy, cryptography,
         bcrypt, pydantic-core, asyncpg don't have a universal wheel
         but DO publish per-(OS, arch) wheels. We emit a Homebrew
         `on_macos`/`on_linux`/`on_arm`/`on_intel` block grouping
         the matching wheels so brew picks the right one at install
         time. Still no compilation.

      c. *sdist fallback* — only if neither (a) nor (b) exists. This
         triggers a from-source compile at brew install time, which
         is precisely what we're trying to avoid for the bulk path.

    The earlier version of this script preferred sdists (step c
    above used to be step a). That was conservative — sdists are
    universal and audit-clean — but it meant scipy / numpy /
    cryptography all ran their full source build during
    `brew install openvox`, which on a stock M-series macOS took
    20-40 min total and often hard-failed at scipy (missing Fortran).
    Wheel-first drops the same install to ~1 min total.

Usage (inside the publish-homebrew job, after PyPI propagation):

    python scripts/gen_homebrew_resources.py openvox-core==0.2.0 > out/resources.rb

The output is a sequence of Homebrew `resource` blocks (and platform
guards) ready to be pasted between the BEGIN/END markers in
`packaging/homebrew/openvox.rb`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path


# Homebrew currently targets these platforms × the Python interpreter
# the formula's `depends_on "python@3.12"` line pulls in. We need a
# wheel whose URL matches both, since a `resource` block has a single
# fixed URL — picking a cp310 wheel here would land an incompatible
# artefact in the cp312 venv at install time.
#
# We accept either:
#   • the version-specific `cp312-cp312-…` tag (3.12 only), or
#   • the stable-ABI `cp3X-abi3-…` tag for X ≤ 12 — abi3 wheels are
#     forward-compatible into newer interpreters, so a cp310-abi3 wheel
#     installs in a 3.12 venv. A `cp313-abi3` wheel would NOT install
#     in 3.12 (backward-incompatible direction), hence the X ≤ 12 cap.
#
# If we ever bump the formula's `depends_on "python@…"` line, update
# both halves of this regex.
PY_TAG_RE = re.compile(r"-(cp312-cp312|cp3(?:[0-9]|1[0-2])-abi3)-")

PLATFORM_TARGETS = {
    "macos_arm": re.compile(r"macosx_\d+_\d+_arm64\.whl$"),
    "macos_intel": re.compile(r"macosx_\d+_\d+_(x86_64|universal2)\.whl$"),
    "linux_x86_64": re.compile(r"manylinux.*_x86_64\.whl$"),
    "linux_aarch64": re.compile(r"manylinux.*_aarch64\.whl$"),
}


@dataclass
class Artefact:
    url: str
    sha256: str


def _resolve(spec: str, python: str, pip_args: list[str]) -> list[dict]:
    """Run pip's dry-run installer for `spec` and return the install report.

    `pip_args` is appended to the pip command. The release workflow uses
    it to add `--extra-index-url https://download.pytorch.org/whl/cpu`
    so we resolve against the CPU-only torch — the default GPU wheel
    pulls in 15+ nvidia-* deps that ship Linux-x86_64 wheels only and
    fail the OS-portable artefact check.
    """
    with tempfile.TemporaryDirectory() as td:
        report = Path(td) / "report.json"
        cmd = [
            python,
            "-m",
            "pip",
            "install",
            "--dry-run",
            "--ignore-installed",
            "--quiet",
            "--progress-bar",
            "off",
            "--report",
            str(report),
            *pip_args,
            spec,
        ]
        subprocess.run(cmd, check=True)
        data = json.loads(report.read_text())
    return data["install"]


def _pypi_release(name: str, version: str) -> list[dict]:
    """Return the `urls` array from PyPI's JSON API for `name==version`."""
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.load(resp)
    return data["urls"]


def _pick_universal_wheel(urls: list[dict]) -> Artefact | None:
    """Return a py3-none-any / py2.py3-none-any wheel if one exists.

    Universal wheels work on every OS + Python version (Python ≥ 3
    in our case — the formula's `depends_on "python@3.12"` is the
    only constraint). Always our first choice: no compile, no
    per-OS block, single URL.
    """
    for u in urls:
        fn = u.get("filename", "")
        if fn.endswith("-py3-none-any.whl") or fn.endswith("-py2.py3-none-any.whl"):
            return Artefact(u["url"], u["digests"]["sha256"])
    return None


def _pick_sdist(urls: list[dict]) -> Artefact | None:
    """Return the sdist if one exists.

    Only reached when neither (a) a universal wheel nor (b) a
    matching per-OS wheel was found. Causes a from-source compile
    at brew install time — fine for header-only / pure-Python
    packages without a wheel, slow for C-extension packages.
    """
    for u in urls:
        if u.get("packagetype") == "sdist":
            return Artefact(u["url"], u["digests"]["sha256"])
    return None


def _pick_platform_wheels(urls: list[dict]) -> dict[str, Artefact]:
    """For each PLATFORM_TARGETS key, return the latest matching wheel.

    "Latest" here just means the first one we encounter that matches —
    PyPI returns one wheel per (cpython, abi, platform) tuple so there's
    typically only one match per platform target anyway.
    """
    picked: dict[str, Artefact] = {}
    for u in urls:
        if u.get("packagetype") != "bdist_wheel":
            continue
        fn = u.get("filename", "")
        if not PY_TAG_RE.search(fn):
            continue
        for plat, regex in PLATFORM_TARGETS.items():
            if plat in picked:
                continue
            if regex.search(fn):
                picked[plat] = Artefact(u["url"], u["digests"]["sha256"])
                break
    return picked


def _emit_resource_block(name: str, art: Artefact, indent: str = "  ") -> str:
    return (
        f'{indent}resource "{name}" do\n'
        f'{indent}  url "{art.url}"\n'
        f'{indent}  sha256 "{art.sha256}"\n'
        f'{indent}end'
    )


def _emit_platform_resource(name: str, wheels: dict[str, Artefact]) -> str:
    """Emit per-platform resource blocks for a wheel-only package.

    The Homebrew DSL allows resource declarations inside `on_macos` /
    `on_linux` / `on_arm` / `on_intel` blocks. They're evaluated lazily
    at install time on the user's machine — only the matching block's
    resources end up in the install list. This is the blessed way to
    ship platform-specific wheels in a single formula.
    """
    parts: list[str] = []
    mac_arm = wheels.get("macos_arm")
    mac_intel = wheels.get("macos_intel")
    if mac_arm or mac_intel:
        parts.append("  on_macos do")
        if mac_arm:
            parts.append("    on_arm do")
            parts.append(_emit_resource_block(name, mac_arm, indent="      "))
            parts.append("    end")
        if mac_intel:
            parts.append("    on_intel do")
            parts.append(_emit_resource_block(name, mac_intel, indent="      "))
            parts.append("    end")
        parts.append("  end")

    lin_x86 = wheels.get("linux_x86_64")
    lin_arm = wheels.get("linux_aarch64")
    if lin_x86 or lin_arm:
        parts.append("  on_linux do")
        if lin_x86:
            parts.append("    on_intel do")
            parts.append(_emit_resource_block(name, lin_x86, indent="      "))
            parts.append("    end")
        if lin_arm:
            parts.append("    on_arm do")
            parts.append(_emit_resource_block(name, lin_arm, indent="      "))
            parts.append("    end")
        parts.append("  end")

    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "spec",
        help="Pinned PyPI spec to resolve, e.g. openvox-core==0.2.0",
    )
    p.add_argument(
        "--exclude",
        default="openvox-core",
        help="Comma-separated package names to skip in the output "
        "(the main package itself, since the formula installs it from "
        "its own `url` line, not a `resource` block).",
    )
    p.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to drive pip's resolver. Defaults to "
        "the running interpreter.",
    )
    p.add_argument(
        "--pip-arg",
        action="append",
        default=[],
        metavar="ARG",
        help="Additional argument to pass to pip's resolver. Repeatable. "
        "Used by the release workflow to add "
        "`--extra-index-url https://download.pytorch.org/whl/cpu` so "
        "the resolver picks CPU-only torch (avoiding 15+ nvidia-* "
        "Linux-only deps that block the OS-portable artefact check).",
    )
    args = p.parse_args(argv)

    skip = {n.strip().lower().replace("_", "-") for n in args.exclude.split(",") if n.strip()}
    installs = _resolve(args.spec, args.python, args.pip_arg)

    portable: list[str] = []
    platform: list[str] = []
    sdist_blocks: list[str] = []
    failed: list[str] = []

    for entry in sorted(installs, key=lambda e: e["metadata"]["name"].lower()):
        meta = entry["metadata"]
        name = meta["name"]
        if name.lower().replace("_", "-") in skip:
            continue

        urls = _pypi_release(name, meta["version"])

        # Pass a: universal pure-Python wheel — best case, single URL,
        # no compile.
        uw = _pick_universal_wheel(urls)
        if uw is not None:
            portable.append(_emit_resource_block(name, uw))
            continue

        # Pass b: per-OS wheels — compiled deps grouped in
        # on_macos/on_linux blocks. Still no compile at install time.
        wheels = _pick_platform_wheels(urls)
        if wheels:
            platform.append(_emit_platform_resource(name, wheels))
            continue

        # Pass c: sdist fallback — last resort, will compile at brew
        # install time.
        sdist = _pick_sdist(urls)
        if sdist is not None:
            sdist_blocks.append(_emit_resource_block(name, sdist))
            continue

        failed.append(f"{name}=={meta['version']}")

    if failed:
        # Wheel-only AND no wheel matching any of our four platform
        # targets — extremely unusual. Refuse to ship a partial formula.
        print(
            "ERROR: PyPI has no portable or platform-matching artefact "
            "for: " + ", ".join(failed),
            file=sys.stderr,
        )
        return 1

    sys.stdout.write("\n".join(portable))
    if platform:
        sys.stdout.write("\n\n")
        sys.stdout.write("  # ── Per-OS wheels (compiled deps; no source build at install time) ──\n")
        sys.stdout.write("\n".join(platform))
    if sdist_blocks:
        sys.stdout.write("\n\n")
        sys.stdout.write("  # ── Sdist fallback (compiled from source by brew at install time) ───\n")
        sys.stdout.write("\n".join(sdist_blocks))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
