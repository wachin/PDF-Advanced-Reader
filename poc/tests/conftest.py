"""Shared fixtures for the PdfCraft-Python proof-of-concept tests."""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
POC = os.path.dirname(HERE)
ROOT = os.path.dirname(POC)
for path in (POC, os.path.join(ROOT, "src")):
    if path not in sys.path:
        sys.path.insert(0, path)

from fixtures import all_fixtures  # noqa: E402


@pytest.fixture(scope="session")
def pdfs(tmp_path_factory) -> dict[str, str]:
    directory = tmp_path_factory.mktemp("pdfcraft_poc")
    return all_fixtures(str(directory))
