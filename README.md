# PDFAR - Advanced PDF Reader

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![PyQt6](https://img.shields.io/badge/PyQt6-6.4+-purple.svg)](https://www.riverbankcomputing.com/software/pyqt/)
[![PyMuPDF](https://img.shields.io/badge/PyMuPDF-1.22+-green.svg)](https://pymupdf.readthedocs.io/)
[![License](https://img.shields.io/badge/License-GPL%203.0-red.svg)](https://www.gnu.org/licenses/gpl-3.0.html)
[![Platform](https://img.shields.io/badge/Platform-Linux-lightgrey.svg)](https://www.linux.org/)

**PDFAR** is a lightweight, advanced PDF reader designed for Linux. It features powerful search capabilities, responsive rendering, and native Qt integration.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| **Advanced Search** | Find content with AND, OR, or exact phrase matching |
| **Page Navigation** | Click results to jump directly to matching pages |
| **Highlighting** | Visual highlights on all matching text |
| **Smooth Scrolling** | Continuous vertical scroll with keyboard shortcuts |
| **Flexible Zoom** | Presets from 50% to 550%, plus keyboard shortcuts |
| **Performance** | Lazy loading and caching for large documents |
| **Multi-language UI** | English, Spanish, with extensible translation support |

---

## 🚀 Quick Start

### Dependencies

```bash
sudo apt update
sudo apt install python3-pyqt6
pip3 install --user PyMuPDF
```

### Run

```bash
python3 run.py
```

---

## 📁 Project Structure

```
PDF-Advanced-Reader/
├── src/pdfar/          # Main application code
│   ├── __init__.py     # Package metadata
│   ├── main.py         # Main window and UI
│   ├── search.py       # Search logic
│   ├── viewer.py       # PDF rendering component
│   ├── cache.py        # Page caching
│   └── i18n.py         # Internationalization support
├── translations/       # Qt Linguist translation files
│   ├── pdfar_en.ts
│   └── pdfar_es.ts
├── debian/             # Debian packaging metadata (future)
├── run.py              # Entry point
├── compile_ts.py       # Translation compiler
└── README.md
```

---

## 🌍 Internationalization

PDFAR supports multiple languages through Qt Linguist:

| Language | Status |
|----------|--------|
| **English** | ✅ Primary |
| **Spanish** | ✅ Complete |
| **More** | 🔧 Extend via `.ts` files |

Add a new language:

1. Copy `pdfar_es.ts` → `pdfar_xx.ts`
2. Edit with Qt Linguist: `linguist pdfar_xx.ts`
3. Compile: `python3 compile_ts.py`

---

## 🛠️ Development

### Translate a New String

In Python code:

```python
# Use strings that are already in English
self.setWindowTitle("PDFAR - Advanced PDF Reader")
```

### Compile Translations

```bash
python3 compile_ts.py
```

---

## 🔧 Roadmap

- [ ] Thumbnail sidebar navigation
- [ ] Persistent annotations (saved highlights/notes)
- [ ] Export search results
- [ ] Dark mode support
- [ ] Full Debian package

---

## 📄 License

**GPL-3.0** — See [LICENSE](LICENSE) for details.

---

## 👤 Author

**Washington Indacochea Delgado**  
Email: linuxfrontier@proton.me

---

## 🤝 Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch
3. Submit a pull request
