"""Lanzador para PyInstaller: usa imports absolutos (un script suelto no admite imports relativos)."""

from __future__ import annotations

import sys

from interpreter.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
