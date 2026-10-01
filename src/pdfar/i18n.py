"""Internationalization support using Qt Linguist."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtCore import QLocale, QTranslator
from PyQt6.QtWidgets import QApplication


class I18n:
    """Manages translations via Qt Linguist (.qm files)."""
    
    def __init__(self, app: QApplication):
        self.app = app
        self.translator = QTranslator(app)
        self._loaded = False
    
    def load_translations(self, lang: str | None = None) -> bool:
        """
        Load translations for the given language code (e.g., 'en', 'es', 'de').
        If lang is None, uses the system locale.
        Returns True if a translation file was successfully loaded.
        """
        if lang is None:
            lang = QLocale.system().name()
            lang = lang.split('_')[0]
        
        qm_path = self._find_qm_file(lang)
        if qm_path:
            try:
                self.translator.load(str(qm_path))
                self.app.installTranslator(self.translator)
                self._loaded = True
                return True
            except Exception:
                pass
        return False
    
    def _find_qm_file(self, lang: str) -> Path | None:
        """Search for the .qm file in standard locations."""
        package_dir = Path(__file__).parent
        parent_dir = package_dir.parent
        translations_dir = parent_dir / "translations"
        
        qm_file = translations_dir / f"pdfar_{lang}.qm"
        
        if qm_file.exists():
            return qm_file
        
        # Check translations directory in current working directory
        cwd_translations = Path.cwd() / "translations" / f"pdfar_{lang}.qm"
        if cwd_translations.exists():
            return cwd_translations
        
        return None
    
    def t(self, context: str, source_text: str, disambiguation: str | None = None, n: int = -1) -> str:
        """
        Translate a string. This is the main translation function.
        
        Args:
            context: Optional context (usually class name or module)
            source_text: The source text to translate
            disambiguation: Optional disambiguation for same strings
            n: Number for plural forms (use tr_n if needed)
        
        Returns:
            The translated string if available, otherwise the source_text
        """
        if self._loaded:
            if n > 0:
                return self.app.translate(context, source_text, disambiguation, n)
            else:
                return self.app.translate(context, source_text, disambiguation)
        return source_text


def tr(source_text: str, disambiguation: str | None = None) -> str:
    """
    Convenience function for translating strings without context.
    For use outside of QObject subclasses.
    """
    from PyQt6.QtCore import QCoreApplication
    return QCoreApplication.translate("PDFAR", source_text, disambiguation)
