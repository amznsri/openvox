"""Enables `python -m openvox.cli <command>` invocation.

Useful for dev / testing without going through `pip install`'s
`[project.scripts]` entry-point stub. The installed `openvox`
binary calls the same `main()`.
"""
from __future__ import annotations

from openvox.cli import main

if __name__ == "__main__":
    main()
