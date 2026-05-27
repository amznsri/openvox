"""Single-source-of-truth check on the package version.

Pre-D.hatch-version: there used to be TWO files that both held the
version string (``pyproject.toml`` + ``openvox/__init__.py``), and
a regex-based test asserted they matched. Bumping releases meant
editing both — error-prone enough that v0.2.6 → v0.2.10 shipped
with the dunder behind by 5 releases (bug #45 family).

Post-D.hatch-version (this commit): ``pyproject.toml`` declares
``dynamic = ["version"]`` and ``[tool.hatch.version]`` points at
``openvox/__init__.py:__version__``. Hatchling reads the dunder
at build time and propagates it into wheel metadata. The dunder
is now the SOLE source of truth — there's nothing for a unit
test to cross-reference against without spinning up an isolated
wheel build.

The post-D.hatch-version verification path:

  1. **This test** asserts the dunder is shaped correctly (PEP
     440-ish: at least three dot-separated numeric components,
     each starting with a digit). Catches typos like ``0,2,5``
     or ``v0.2.11`` before the release pipeline starts spending
     time on broken metadata.

  2. **The release pipeline's ``verify-pypi-install`` job**
     (CLAUDE.md §8 #94) installs the freshly-published wheel
     into a clean venv, runs ``openvox version``, and asserts
     the output equals the new version. That's the genuine
     end-to-end check that hatchling's dynamic-version hook
     fired correctly — done against a real install, not the
     source tree.

This file used to also assert ``importlib.metadata.version(
"openvox-core") == openvox.__version__`` to catch a regressed
hatch hook. Removed because it false-positives on editable
``pip install -e .`` dev installs (the dist-info metadata
gets pinned at install time and lags the source dunder on
every bump). The verify-pypi-install CI job is the real
check.
"""

from __future__ import annotations

import openvox


def test_dunder_version_is_pep440_shape():
    """Catch typos like '0,2,5' or 'v0.2.11' before they hit PyPI.

    Loose check: at least three dot-separated numeric components,
    each component starts with a digit (allows trailing tags
    like ``rc1`` / ``post1`` / ``dev0`` after the digit).
    """
    parts = openvox.__version__.split(".")
    assert len(parts) >= 3, f"unexpected version shape: {openvox.__version__!r}"
    for p in parts:
        assert p and p[0].isdigit(), (
            f"version component {p!r} doesn't start with a digit"
        )
