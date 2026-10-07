# Large / Scanned PDF Performance Engineering

> **Status**: Implemented ([x]) — milestone complete
> **Date**: 2026-10-07
> **Benchmark source**: `tools/benchmark.py`

---

## 1. Problem Statement

PDFAR's original architecture worked well for small to medium text-based PDFs but exhibited severe performance problems with large, image-heavy, or scanned PDFs (100+ pages, high-resolution images):

- **Opening latency**: 2.78 s before first interaction
- **First visible pixels**: 41.47 s for a 540-page scanned PDF
- **Peak memory**: ~4.8 GB RSS
- **Unnecessary work**: 2,052 render jobs queued at open (mostly thumbnails for invisible pages)
- **Zoom operations**: 19 s for 100% → 200% → 300%

The root cause was **eager, synchronous work during `open_document()`** combined with an **inefficient render result transport** (PNG encode/decode).

---

## 2. Baseline Measurements

Test document: *LibreOffice Getting Started Guide (GS262-GettingStarted.pdf)* — 540 pages, 24 MB, mixed text + high-res images.

| Metric | Baseline (Before) |
|---|---:|
| open_document_time | 2.78 s |
| jobs_queued_during_open | 2,052 |
| time_to_first_page | 41.47 s |
| time_to_viewport_complete | 0.36 s |
| jump_to_distant_page | 0.96 s |
| zoom_sequence (100→200→300%) | 19.4 s |
| peak_rss_mb | 4,783 MB |

Measurements taken with `tools/benchmark.py` (headless offscreen Qt).

---

## 3. Identified Bottlenecks

Profiling with `tools/profile_open.py` and manual instrumentation revealed:

| Bottleneck | Time | Description |
|---|---:|---|
| `Sidebar._build_thumbs()` | ~1.0 s | Created 540 QListWidgetItems + queued 540 thumbnail render jobs |
| PNG encode/decode in workers | ~180x overhead | `pix.tobytes("png")` + `QImage.loadFromData(png)` per render |
| `AnnotationManager._load_from_pdf()` | ~0.12 s | Scanned all 540 pages for annotations synchronously |
| `PDFView` page size enumeration | ~0.12 s | `doc.load_page(i).rect` instead of `doc[i].rect` |
| Memory level "greedy" default | — | Persisted from previous session, caused full preload |

---

## 4. Implemented Optimizations

### 4.1 Raw Pixel Buffer Transport (`render.py`)

**Before**: Worker rendered via `pix.tobytes("png")`, emitted PNG bytes, GUI decoded with `QImage.loadFromData(png)`.

**After**: Worker returns raw pixel samples:

```python
# Worker thread
pix = page.get_pixmap(matrix=mat, alpha=job.alpha, annots=True, clip=clip)
samples = bytes(pix.samples)
return RenderResult(
    samples=samples,
    stride=pix.stride,
    alpha=pix.alpha,
    # ... other fields
)
```

```python
# GUI thread
img = result.to_qimage()  # QImage(samples, w, h, stride, fmt).copy()
```

**Result**: ~182x faster render result delivery (7.87 s → 0.04 s for 50 renders).

### 4.2 Lazy Thumbnail Rendering (`sidebar.py`)

**Before**: `_build_thumbs()` iterated all pages, created all items, queued all thumbnail renders (priority 8).

**After**:
- `_build_thumbs()` only creates placeholder items and computes zoom factors
- `_request_visible_thumbs()` queues thumbnails only for visible viewport items (+ small margin)
- Connected to `verticalScrollBar.valueChanged`, `tabs.currentChanged`, `showEvent`, `resizeEvent`
- Stale thumbnail jobs cancelled via `pool.cancel_if(predicate)`

**Result**: Opening a 540-page document queues ~5 thumbnails instead of 540.

### 4.3 Memory Level Default Fix (`main.py`)

**Bug**: `_restore_state()` read persisted `memory_level` from `QSettings`. If user previously selected "Greedy", it persisted and caused full-document preload on every subsequent open.

**Fix**: Ensure default is "normal" and persist only on explicit user change.

### 4.4 Faster Page Metadata (`viewer.py`)

```python
# Before
r = self.doc.load_page(i).rect
self.page_sizes.append((r.width, r.height))

# After
r = self.doc[i].rect
self.page_sizes.append((r.width, r.height))
```

**Result**: 0.123 s → 0.041 s for 540 pages (~3x faster).

### 4.5 DisplayList Caching (`render.py` worker loop)

```python
doc = getattr(tls, "doc", None)
if doc is None or getattr(tls, "path", None) != self.doc_path:
    # ... open new doc ...
else:
    # Reuse existing document
    pass
page = doc.load_page(job.page)
# For repeated zooms on same page, DisplayList avoids re-parsing
```

**Result**: ~2x speedup for zoom changes; 1.04x for tiles.

### 4.6 Visible-Content-First Rendering (`viewer.py`)

`_request_visible()` already implemented priority-based rendering:
- Priority 0: visible viewport tiles/pages
- Priority 1: immediate adjacent content
- Priority 2: near prefetch
- Priority 3+: background

`cancel_if()` removes stale jobs when viewport moves.

---

## 5. After Measurements

| Metric | Before | After | Improvement |
|---|---:|---:|---:|
| open_document_time | 2.78 s | 1.96 s | **30% faster** |
| jobs_queued_during_open | 2,052 | 0 | **Eliminated** |
| time_to_first_page | 41.47 s | 0.66 s | **63x faster** |
| time_to_viewport_complete | 0.36 s | 0.0003 s | **1200x faster** |
| jump_to_distant_page | 0.96 s | 0.43 s | **2.2x faster** |
| zoom_sequence | 19.4 s | 1.81 s | **10.7x faster** |
| peak_rss_mb | 4,783 MB | 642 MB | **7.5x less memory** |

---

## 6. Why These Techniques Work

### 6.1 Lazy Thumbnail Rendering

Okular and other high-performance viewers never render all thumbnails at open. They treat the thumbnail sidebar as a virtualized list:

- Only items intersecting the viewport (plus a small prefetch margin) are rendered
- Scrolling triggers new requests; items leaving viewport have their jobs cancelled
- Placeholder icons avoid layout shifts

PDFAR now follows this pattern exactly.

### 6.2 Raw Pixel Transport vs PNG

PNG encoding is CPU-intensive (deflate compression). For a 5000×5000 scanned page at 2× zoom:
- PNG encode: ~150 ms
- PNG decode: ~80 ms
- Raw buffer copy: < 1 ms

At 540 pages, the cumulative savings are massive. The tradeoff is slightly larger memory per render result (uncompressed RGBA/RGB), but the memory budget is bounded by the existing `PageCache` and tile eviction.

### 6.3 DisplayList Caching

PyMuPDF's `DisplayList` is a retained drawing command list. Creating it parses the page's content stream once. Subsequent `get_pixmap()` calls at different matrices replay the list instead of re-parsing.

For zoom operations (same page, different matrix), this is a significant win. For tiles (different clips, same matrix), the benefit is smaller but still present.

### 6.4 Rendering Priorities & Cancellation

The `RenderPool` priority queue ensures:
- Visible content is always at the front
- Background prefetch never starves visible pages
- `cancel_if()` removes jobs that are no longer relevant (e.g., user scrolled away)

This is the same principle Okular uses: "render what the user sees now; everything else is optional and cancellable."

---

## 7. Memory Usage Changes

| Component | Before | After |
|---|---|---|
| PageCache (full pages) | Unbounded growth | Bounded (256 MB default) |
| Tile cache (`_tiles`) | Unbounded | Still unbounded* |
| Thumbnail jobs in pool | 2,052 | ~5 |
| Worker document handles | 4 | 4 (unchanged) |

* Tile cache eviction is a known remaining task. In practice, memory stays reasonable because tiles are only created for large pages at high zoom, and zoom changes clear the tile cache.

---

## 8. Running the Benchmark

```bash
# Run the performance benchmark on any PDF
python3 tools/benchmark.py path/to/your.pdf

# Profile the opening path breakdown
python3 tools/profile_open.py
```

Requirements:
- `QT_QPA_PLATFORM=offscreen` (set automatically by the scripts)
- PyQt6, PyMuPDF, pytest

---

## 9. Remaining Performance Work

| Item | Status | Description |
|---|---|---|
| Tile cache memory budget | [~] Partially | Tiles are not bounded by byte budget; should use unified cache policy |
| AnnotationManager lazy loading | [ ] Not implemented | Scans all pages on init (~0.12 s for 540 pages) |
| Bounded DisplayList per-worker cache | [ ] Not implemented | DisplayList cache grows with unique pages/zooms |
| Manual Okular comparison | [ ] Not implemented | Controlled side-by-side benchmark not yet performed |

---

## 10. Architecture Philosophy

The guiding principle for this milestone:

> **Render almost nothing that the user does not currently need. Render what the user sees immediately. Reuse what has already been parsed/rendered. Cancel work that is no longer useful. Keep memory bounded.**

This is the same demand-driven philosophy used by Okular and other high-performance document viewers.

---

## 11. Files Modified

| File | Changes |
|---|---|
| `src/pdfar/render.py` | Raw buffer transport, DisplayList caching, `RenderResult.to_qimage()` |
| `src/pdfar/sidebar.py` | Lazy thumbnail loading, viewport-aware requests, cancellation |
| `src/pdfar/viewer.py` | Faster page size access (`doc[i]`) |
| `tests/test_render.py` | Updated for new `RenderResult` format |

### New Files Created

- `tools/benchmark.py` — Comprehensive performance benchmark
- `tools/profile_open.py` — Opening path profiling
- `docs/en/developers/performance/large-pdf-performance.md` — This document

---

## 12. Verification

- All 131 existing tests pass (3 consecutive runs)
- Manual testing with 540-page scanned PDF confirms dramatic responsiveness improvement
- Highlight + note PDF persistence verified working
- No regressions in small PDF performance

---

## 13. References

- `docs/okular-architecture.md` — Okular architecture study that inspired the demand-driven approach
- `tools/benchmark.py` — Reproducible benchmark methodology
- `tools/profile_open.py` — Opening path breakdown