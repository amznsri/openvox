"""Pin the package version to a single source of truth.

There used to be THREE places hosting the version string:
  - ``packages/core/pyproject.toml`` (what pip resolves)
  - ``packages/core/openvox/__init__.py:__version__``
  - ``packages/core/openvox/api/app.py`` (the FastAPI app's version=)

The v0.2.6 → v0.2.10 release sweep bumped the first two but missed
``__init__.py`` for 5 releases, causing ``/health`` to report 0.2.5
on machines actually running 0.2.10 (see v0.2.11 release notes).

v0.2.12 consolidates: ``app.py`` now reads ``__version__`` from
``openvox/__init__.py``, so the only TWO files that need to change
on a version bump are pyproject.toml and __init__.py. This test
asserts those two stay in lockstep — if a bump touches only one,
the unit suite fails on every cell of the matrix and the contributor
sees the mismatch before pushing.

Long-term: pyproject.toml could also read from __init__ via a
build-time hatchling hook (``[tool.hatch.version]`` dynamic).
That's a v0.2.13+ follow-up; this test is the bridge.
"""

from __future__ import annotations

import pathlib
import re

import openvox


def _pyproject_version() -> str:
    """Read ``version`` out of ``pyproject.toml`` without depending on
    a TOML parser at test time (tomllib is 3.11+ but pinning the
    parsing logic to the regex keeps the test trivial)."""
    here = pathlib.Path(__file__).resolve().parent
    pyproject = here.parent / "pyproject.toml"
    text = pyproject.read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    assert m, f"no version line found in {pyproject}"
    return m.group(1)


def test_pyproject_matches_dunder_version():
    """pyproject.toml's `version = "X.Y.Z"` MUST equal openvox.__version__."""
    pyproject_v = _pyproject_version()
    dunder_v = openvox.__version__
    assert pyproject_v == dunder_v, (
        f"version mismatch — pyproject.toml says {pyproject_v!r} but "
        f"openvox/__init__.py says {dunder_v!r}. Bump BOTH together; "
        f"that's the convention until the build-time hatchling hook lands."
    )


def test_dunder_version_is_pep440_shape():
    """Catch typos like '0,2,5' or 'v0.2.11' before they hit PyPI.

    Loose check: at least three dot-separated numeric components.
    """
    parts = openvox.__version__.split(".")
    assert len(parts) >= 3, f"unexpected version shape: {openvox.__version__!r}"
    # Each component starts with digits (allow trailing tags like "0rc1").
    for p in parts:
        assert p and p[0].isdigit(), (
            f"version component {p!r} doesn't start with a digit"
        )
