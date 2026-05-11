"""Tiny CLI alias so `openvox-core` works as a script entry-point.

Just calls main() — same as `python main.py`.
"""

from __future__ import annotations


def main() -> None:
    from main import main as _main

    _main()


if __name__ == "__main__":
    main()
