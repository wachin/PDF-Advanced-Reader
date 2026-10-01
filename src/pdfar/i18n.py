"""Optional internationalization via Qt Linguist.

The UI currently ships English strings; this module keeps the plumbing for
loading ``translations/pdfar_<lang>.qm`` files so a translation can be dropped
in without code changes.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QLocale, QTranslator
from PyQt6.QtWidgets import QApplication


class I18n:
    """Loads Qt Linguist translations for the application."""

    def __init__(self, app: QApplication):
        self.app = app
        self.translator = QTranslator(app)
        self._loaded = False

    def load_translations(self, lang: str | None = None) -> bool:
        if lang is None:
            lang = QLocale.system().name().split("_")[0]
        qm_path = self._find_qm_file(lang)
        if qm_path and self.translator.load(str(qm_path)):
            self.app.installTranslator(self.translator)
            self._loaded = True
            return True
        return False

    def _find_qm_file(self, lang: str) -> Path | None:
        candidates = [
            Path(__file__).resolve().parent.parent / "translations" / f"pdfar_{lang}.qm",
            Path.cwd() / "translations" / f"pdfar_{lang}.qm",
        ]
        for path in candidates:
            if path.exists():
                return path
        return None

    @property
    def loaded(self) -> bool:
        return self._loaded

    def t(self, context: str, source_text: str, disambiguation: str | None = None) -> str:
        if self._loaded:
            return self.app.translate(context, source_text, disambiguation)
        return source_text
