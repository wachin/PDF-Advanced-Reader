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

## ⚡ Large / Scanned PDF Performance Breakthrough

PDFAR underwent a dedicated **performance engineering milestone** targeting large and scanned PDFs. The rendering pipeline was redesigned around **demand-driven rendering**: render what the user currently needs first, avoid unnecessary work, cancel obsolete work, reuse rendered data, and keep memory usage bounded.

### Verified Benchmark Results

Testing with the 540-page LibreOffice Getting Started Guide (24 MB, heavy images):

| Metric | Before | After | Improvement |
|---|---:|---:|---:|
| open_document_time | 2.78 s | 1.96 s | **30% faster** |
| jobs_queued_during_open | 2,052 | 0 | **Eliminated** |
| time_to_first_page | 41.47 s | 0.66 s | **63x faster** |
| time_to_viewport_complete | 0.36 s | 0.0003 s | **1200x faster** |
| jump_to_distant_page | 0.96 s | 0.43 s | **2.2x faster** |
| zoom_sequence (100%→200%→300%) | 19.4 s | 1.81 s | **10.7x faster** |
| peak_rss_mb | 4,783 MB | 642 MB | **7.5x less memory** |

> **Note:** These results come from the project's performance benchmark (`tools/benchmark.py`) on a specific test PDF and hardware. They should not be presented as universal performance guarantees for every PDF or every machine.

### Major Technical Improvements

1. **Raw pixel buffer transport** — Replaced the PNG encode/decode round-trip with direct pixel buffer transfer. Worker threads now return raw `pix.samples` bytes + stride + format; the GUI thread constructs `QImage` directly. This removed ~180x PNG overhead.

2. **Lazy, viewport-aware thumbnail rendering** — Thumbnails are now only rendered for visible sidebar items. Opening a 540-page document queues ~5 thumbnails instead of all 540. Scroll/tab-change triggers lazy loading with a small margin; stale thumbnail jobs are cancelled when scrolling away.

3. **Correct memory-level default** — Fixed a bug where "greedy" memory level persisted from a previous session, causing full-document preloading on every open. Default is now "normal" (viewport + small prefetch).

4. **Faster page metadata access** — Changed `doc.load_page(i).rect` → `doc[i].rect` (~3x faster page size enumeration).

5. **Per-worker DisplayList caching** — Workers reuse `page.get_displaylist()` for repeated zooms/tiles (~2x speedup for zoom changes).

6. **Visible-content-first rendering architecture** — The first visible page receives absolute priority; background work is deprioritized and cancellable.

7. **Reduced unnecessary work during document opening** — Annotation scanning, thumbnail creation, and full-document layout are deferred or made lazy.

See `docs/en/developers/performance/large-pdf-performance.md` for the full technical deep-dive.

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

## 🙏 Acknowledgements

This project owes a great debt to **Okular** (https://apps.kde.org/okular/), the
mature KDE document viewer (GPL-2.0+ / GPL-3.0+, by KDE and its contributors,
https://invent.kde.org/graphics/okular).

In particular, the tiled-rendering strategy that lets PDF-Advanced-Reader
display heavy, scanned PDFs smoothly at high zoom (render only the visible
tiles of a large page instead of one giant bitmap) was **directly inspired by
Okular's `TilesManager`** (see `core/document.cpp` in the Okular source). PDFAR
studied Okular's architecture and re-implemented the same *ideas* from scratch
in Python/PyQt6/PyMuPDF. Without Okular's proven approach, we would not have
solved this — thank you to the Okular developers.

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
