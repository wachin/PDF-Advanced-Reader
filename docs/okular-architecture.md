# Research: Okular Architecture — Document Loading and Scrolling

> **Source:** source code in `external/okular/` (read-only, see AGENTS.md).
> **Goal:** replicate these techniques in PDF-Advanced-Reader (Python/PyQt6/PyMuPDF)
> so that PDFs with scanned (heavy) pages open fast and scroll smoothly,
> just like in Okular.

---

## 1. Instant loading: only metadata on open

**Okular does NOT render anything when opening a PDF.**

`generators/poppler/generator_pdf.cpp` → `PDFGenerator::loadPages()` (line 880):

- When opening the document it only reads **per-page metadata**:
  - `p->pageSizeF()` — dimensions
  - `p->orientation()` — rotation
  - annotations, links, form fields, duration, label
- It **never** calls `get_pixmap()` / render during opening.
- The page size is used to create **geometric placeholders**
  (`new Okular::Page(i, w, h, orientation)`).

**PDFAR equivalent:** we already do this correctly — `PDFViewer._init_all_pages()`
creates all widgets with `page.rect` × zoom. The opening cost is only
reading the PDF's xref, not rendering.

---

## 2. Viewport-driven on-demand rendering

`part/pageview.cpp` → `PageView::requestVisiblePixmaps()` (~line 4950).
It is invoked on every viewport change (scroll, zoom, resize):

1. Computes `viewportRect` from the widget's **own** scrollbars
   (`QAbstractScrollArea`: `horizontalScrollBar()->value()`,
   `verticalScrollBar()->value()`, `viewport()->width/height()`).
2. Iterates all page items and selects those that **intersect** the
   viewport (`viewportRect.intersected(i->croppedGeometry())`).
3. Only for visible pages (or in the preload zone) it emits
   `PixmapRequest` → `d->document->requestPixmaps(requestedPixmaps)`.

**Key point:** there is no fixed "render first 10 pages". It renders
exactly what the user is looking at.

---

## 3. Preload with a 512 px margin (not a fixed N pages)

In `requestVisiblePixmaps()`:

```cpp
// Margin (in pixels) around the viewport to preload
const int pixelsToExpand = 512;
```

- Each visible page expands its rect with **512 extra px** to request tiles
  around what is visible.
- For full-page preload: `pagesToPreload = viewColumns()`
  (normally 1–2 pages before and after the visible ones), and the
  viewport window is expanded `±pixelsToExpand` vertically
  (`viewportRect.adjusted(0, -pixelsToExpand, 0, pixelsToExpand)`).
- It depends on the configured memory level
  (`Okular::Settings::memoryLevel()`):
  - `Low` → no preload
  - `Normal` → 1–2 surrounding pages
  - `Greedy` → all pages

**Lesson for PDFAR:** our `PREFETCH_BACK=2 / PREFETCH_FORWARD=8` in
pages is arbitrary; Okular uses **pixels** (512 px ≈ half a screen) and
limits preload according to available memory.

---

## 4. Priority request queue + cancellation

`core/document.cpp` → `DocumentPrivate::sendGeneratorPixmapRequest()` (line 1324):

- `PixmapRequest` objects are stacked in `m_pixmapRequestsStack` and processed
  **in priority order** (priority 0 = visible, higher = farther preload).
- Before rendering a request it decides:
  - Does the pixmap already exist? (`page()->hasPixmap(...)`) → discard.
  - Is it already being generated? (`tilesManager->isRequesting(...)`) → discard.
  - Is it a preload that doesn't fit in cache? (`qAbs(page - currentViewportPage) >= maxDistance`) → discard.
- **Cancellation of obsolete renders:**
  `shouldCancelRenderingBecauseOf()` (line 3169) + `cancelRenderingBecauseOf()`
  — if an in-flight render is no longer relevant (e.g. fast scrolling), it is
  cancelled in favor of the new request.
- After launching a render, it schedules the next one with
  `QTimer::singleShot(30, ...)` — a small rate-limit that avoids saturation.

---

## 5. One dedicated QThread per render, never blocks the GUI

`core/generator_p.h` (line 109) → `PixmapGenerationThread : public QThread`:

- Each page render runs in its **own QThread**
  (`startGeneration()` → `start()` → `run()` calls `mGenerator->image(mRequest)`).
- The result reaches the observers via signals
  (`DocumentObserver::pageChanged`), always on the GUI thread.
- The PDF generator declares `setFeature(Threaded)` and `setFeature(TiledRendering)`
  (`generator_pdf.cpp:675,687`) — preload only works if the generator is
  Threaded (`document.cpp:1369`).

---

## 6. Tiled rendering for large pages (key for scans!)

`document.cpp` (~line 1404):

```cpp
// If the requested area is above 4*screenSize pixels, and we're not
// rendering most of the page, switch on the tile manager
else if (!tilesManager && m_generator->hasFeature(Generator::TiledRendering)
         && (long)r->width() * (long)r->height() > 4L * screenSize
         && normalizedArea < 0.75) {
    // if the image is too big. start using tiles
```

- If the full-page bitmap exceeds **4× the screen size**
  (typical in high-resolution scans with zoom), the whole page is not rendered:
  the `TilesManager` kicks in (`core/tilesmanager_p.h`).
- **Only the visible tiles** are rendered (`NormalizedRect` of the viewport
  expanded by 512 px) at the requested size.
- `Tile` = normalized rect + QPixmap + validity flag; tiles are
  reused as long as the zoom doesn't change.

**This is exactly why scanned PDFs "look good while scrolling" in Okular:**
it never renders a 20000 px-tall page, only the ~screen-size chunks you see.

---

## 7. Memory cache with a dynamic limit

`core/document.cpp`:

- `calculateMemoryToFree()` (line ~253): cache limit =
  `getTotalMemory() / 3` and it also considers **free memory and swap**
  (`qMin(qMax(freeMemory, getTotalMemory()/2), freeMemory + freeSwap)`).
- `cleanupPixmapMemory()`: evicts from cache the pixmaps with
  **lowest priority and farthest from the viewport**
  (`searchLowestPriorityPixmap(true)`), until enough memory is freed.
- Each pixmap's priority is updated with its distance to the current
  viewport on every page change.

**Lesson:** PDFAR's cache (fixed: 30 pages) should be **byte-based**
with a limit proportional to RAM, evicting the farthest pages first.

---

## 8. Scrolling: QAbstractScrollArea + scrollContentsBy

`part/pageview.cpp`:

- `PageView` inherits from `QAbstractScrollArea` — the scroll is **owned by
  the widget**, it doesn't depend on the parent (same diagnosis we made for Bug 1.1).
- `PageView::scrollContentsBy(dx, dy)` (line 3509) is minimal: just
  `viewport()->scroll()` + repaints damaged regions. **It renders nothing there.**
- Rendering is triggered by `requestVisiblePixmaps()` via viewport events
  (`slotViewport()`), with `QScroller` for smooth scroll physics
  (`d->scroller = QScroller::scroller(viewport())`, line 406).

---

## 9. Rotation and parallel image jobs

`core/pagecontroller.cpp`: image rotations are queued in a
`ThreadWeaver::Queue` (KDE thread pool), results delivered via the
`rotationFinished(page, okularPage)` signal.

---

## Summary: what PDF-Advanced-Reader should adopt

| # | Okular technique | Status in PDFAR | Proposed action |
|---|------------------|-----------------|-----------------|
| 1 | Opening = metadata only | ✅ Already done (placeholders) | Keep |
| 2 | Rendering driven by the real viewport (intersection with viewportRect) | ⚠️ Partial (`_get_visible_page_range` with cached positions) | Compute real viewport↔page intersection |
| 3 | Preload in **pixels** (512 px) limited by memory | ❌ Fixed page count (2 back/8 forward) | Convert to px margin + memory level |
| 4 | Priority request queue + cancellation of obsolete renders | ❌ No cancellation | Implement priority queue with cancel flag |
| 5 | Async render in a separate thread, result delivered to GUI via signal | ⚠️ We have ThreadPoolExecutor + QTimer.singleShot | Verify the GUI never receives updates from another thread |
| 6 | **Tiles** when page > 4× screen | ❌ Doesn't exist | Tile-based rendering for high zoom/scans |
| 7 | **Byte-based** cache with limit = RAM/3 and priority-based eviction | ⚠️ Fixed 30-page cache | Cache with memory limit in MB |
| 8 | QAbstractScrollArea with its own scroll | ⚠️ We use a wrapping QScrollArea | Migrate PDFViewer to QAbstractScrollArea |
| 9 | `scrollContentsBy()` doesn't render; render on viewport event | ⚠️ We render in the scroll timer | Separate: scroll → paint only; viewport-change → request pixmaps |

---

## Exact references (for future consultation)

| Topic | File | Approx. lines |
|-------|------|---------------|
| Open without rendering | `generators/poppler/generator_pdf.cpp` | `loadPages()` 880–935 |
| Viewport-driven requests | `part/pageview.cpp` | `requestVisiblePixmaps()` ~4950–5130 |
| 512 px preload | `part/pageview.cpp` | 4970, 5077–5080 |
| Request queue | `core/document.cpp` | `sendGeneratorPixmapRequest()` 1324–1430 |
| Cancellation | `core/document.cpp` | 3169–3252 |
| Tiles switch-on | `core/document.cpp` | ~1404 |
| Render threads | `core/generator_p.h/.cpp` | 109–146 / 56–60 |
| Memory cache | `core/document.cpp` | 253–330 |
| Scroll handling | `part/pageview.cpp` | `scrollContentsBy()` 3509, QScroller 406 |
