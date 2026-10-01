#!/usr/bin/env python3
"""Entry point: run PDFAR from a source checkout.

    python3 run.py                # file dialog
    python3 run.py file.pdf       # open a document
"""

import os
import sys

src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from pdfar.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
