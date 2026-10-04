# Investigación: Arquitectura de Okular — Carga de documentos y scroll

> **Fuente:** código fuente en `external/okular/` (solo lectura, ver AGENTS.md).
> **Objetivo:** replicar estas técnicas en PDF-Advanced-Reader (Python/PyQt6/PyMuPDF)
> para que PDFs con páginas escaneadas (pesadas) se abran rápido y el scroll
> sea fluido, igual que en Okular.

---

## 1. Carga instantánea: solo metadatos al abrir

**Okular NO renderiza nada al abrir un PDF.**

`generators/poppler/generator_pdf.cpp` → `PDFGenerator::loadPages()` (línea 880):

- Al abrir el documento solo lee **metadatos por página**:
  - `p->pageSizeF()` — dimensiones
  - `p->orientation()` — rotación
  - anotaciones, links, form fields, duración, label
- **Nunca** llama a `get_pixmap()` / render durante la apertura.
- El tamaño de página se usa para crear **placeholders geométricos**
  (`new Okular::Page(i, w, h, orientation)`).

**Equivalente en PDFAR:** esto ya lo hacemos bien — `PDFViewer._init_all_pages()`
crea todos los widgets con `page.rect` × zoom. El costo de apertura es solo
leer el xref del PDF, no renderizar.

---

## 2. Render bajo demanda dirigido por el viewport

`part/pageview.cpp` → `PageView::requestVisiblePixmaps()` (~línea 4950).
Se invoca en cada cambio de viewport (scroll, zoom, resize):

1. Calcula `viewportRect` desde los scrollbars **propios**
   (`QAbstractScrollArea`: `horizontalScrollBar()->value()`,
   `verticalScrollBar()->value()`, `viewport()->width/height()`).
2. Itera todos los items de página y selecciona los que **intersectan** el
   viewport (`viewportRect.intersected(i->croppedGeometry())`).
3. Solo para páginas visibles (o en la zona de preload) emite
   `PixmapRequest` → `d->document->requestPixmaps(requestedPixmaps)`.

**Clave:** no hay "renderizar primeras 10 páginas" fijo. Se renderiza
exactamente lo que el usuario está viendo.

---

## 3. Preload con margen de 512 px (no N páginas fijas)

En `requestVisiblePixmaps()`:

```cpp
// Margin (in pixels) around the viewport to preload
const int pixelsToExpand = 512;
```

- Cada página visible expande su rect con **512 px extra** para pedir tiles
  alrededor de lo visible.
- Para preload de páginas completas: `pagesToPreload = viewColumns()`
  (normalmente 1–2 páginas antes y después de las visibles), y la ventana de
  viewport se expande `±pixelsToExpand` verticalmente
  (`viewportRect.adjusted(0, -pixelsToExpand, 0, pixelsToExpand)`).
- Depende del nivel de memoria configurado
  (`Okular::Settings::memoryLevel()`):
  - `Low` → sin preload
  - `Normal` → 1–2 páginas alrededor
  - `Greedy` → todas las páginas

**Lección para PDFAR:** nuestro `PREFETCH_BACK=2 / PREFETCH_FORWARD=8` en
páginas es arbitrario; Okular usa **píxeles** (512 px ≈ media pantalla) y
limita preload según la memoria disponible.

---

## 4. Cola de requests con prioridad + cancelación

`core/document.cpp` → `DocumentPrivate::sendGeneratorPixmapRequest()` (línea 1324):

- Los `PixmapRequest` se apilan en `m_pixmapRequestsStack` y se procesan
  **en orden de prioridad** (priority 0 = visible, mayor = preload lejano).
- Antes de renderizar un request decide:
  - ¿Ya existe el pixmap? (`page()->hasPixmap(...)`) → descarta.
  - ¿Ya se está generando? (`tilesManager->isRequesting(...)`) → descarta.
  - ¿Es preload y no cabe en cache? (`qAbs(page - currentViewportPage) >= maxDistance`) → descarta.
- **Cancelación de renders obsoletos:**
  `shouldCancelRenderingBecauseOf()` (línea 3169) + `cancelRenderingBecauseOf()`
  — si un render en curso ya no es relevante (p. ej. scroll rápido), se cancela
  a favor del request nuevo.
- Tras lanzar un render, agenda el siguiente con
  `QTimer::singleShot(30, ...)` — un pequeño rate-limit que evita saturar.

---

## 5. Un QThread dedicado por render, nunca bloquea la GUI

`core/generator_p.h` (línea 109) → `PixmapGenerationThread : public QThread`:

- Cada render de página corre en su **propio QThread**
  (`startGeneration()` → `start()` → `run()` llama `mGenerator->image(mRequest)`).
- El resultado llega a los observers por señales
  (`DocumentObserver::pageChanged`), siempre en el hilo GUI.
- El generador PDF declara `setFeature(Threaded)` y `setFeature(TiledRendering)`
  (`generator_pdf.cpp:675,687`) — el preload solo funciona si el generador es
  Threaded (`document.cpp:1369`).

---

## 6. Tiled rendering para páginas grandes (¡clave para escaneos!)

`document.cpp` (~línea 1404):

```cpp
// If the requested area is above 4*screenSize pixels, and we're not
// rendering most of the page, switch on the tile manager
else if (!tilesManager && m_generator->hasFeature(Generator::TiledRendering)
         && (long)r->width() * (long)r->height() > 4L * screenSize
         && normalizedArea < 0.75) {
    // if the image is too big. start using tiles
```

- Si el bitmap de la página completa supera **4× el tamaño de la pantalla**
  (típico en escaneos a alta resolución con zoom), no se renderiza la página
  entera: entra el `TilesManager` (`core/tilesmanager_p.h`).
- Se renderizan **solo los tiles visibles** (`NormalizedRect` del viewport
  expandido 512 px) en el tamaño pedido.
- `Tile` = rect normalizado + QPixmap + flag de validez; los tiles se
  reutilizan mientras el zoom no cambie.

**Esto es exactamente por qué los PDFs escaneados "se ven bien al hacer
scroll" en Okular:** nunca renderiza una página de 20000 px de alto, solo
los trozos de ~screen-size que ves.

---

## 7. Cache de memoria con límite dinámico

`core/document.cpp`:

- `calculateMemoryToFree()` (línea ~253): límite de cache =
  `getTotalMemory() / 3` y además considera **memoria libre y swap**
  (`qMin(qMax(freeMemory, getTotalMemory()/2), freeMemory + freeSwap)`).
- `cleanupPixmapMemory()`: desaloja del cache los pixmaps de
  **menor prioridad y más lejanos del viewport**
  (`searchLowestPriorityPixmap(true)`), hasta liberar lo necesario.
- La prioridad de cada pixmap se actualiza con la distancia al viewport
  actual en cada cambio de página.

**Lección:** el cache de PDFAR (fijo: 30 páginas) debería ser por **bytes**
con límite proporcional a la RAM, desalojando primero lo más lejos.

---

## 8. Scroll: QAbstractScrollArea + scrollContentsBy

`part/pageview.cpp`:

- `PageView` hereda de `QAbstractScrollArea` — el scroll es **propio del
  widget**, no depende del padre (mismo diagnóstico que hicimos para el Bug 1.1).
- `PageView::scrollContentsBy(dx, dy)` (línea 3509) es mínimo: solo
  `viewport()->scroll()` + repinta regiones dañadas. **No renderiza nada ahí.**
- El render lo dispara `requestVisiblePixmaps()` vía eventos de viewport
  (`slotViewport()`), con `QScroller` para física de scroll suave
  (`d->scroller = QScroller::scroller(viewport())`, línea 406).

---

## 9. Rotación y trabajos paralelos de imagen

`core/pagecontroller.cpp`: rotaciones de imágenes se encolan en un
`ThreadWeaver::Queue` (pool de hilos KDE), resultados por señal
`rotationFinished(page, okularPage)`.

---

## Resumen: qué debe adoptar PDF-Advanced-Reader

| # | Técnica Okular | Estado en PDFAR | Acción propuesta |
|---|----------------|-----------------|------------------|
| 1 | Apertura = solo metadatos | ✅ Ya la tenemos (placeholders) | Mantener |
| 2 | Render dirigido por viewport real (intersección con viewportRect) | ⚠️ Parcial (`_get_visible_page_range` con posiciones cacheadas) | Calcular intersección real viewport↔página |
| 3 | Preload en **píxeles** (512 px) limitado por memoria | ❌ Fijo en nº de páginas (2 atrás/8 delante) | Convertir a margen en px + nivel de memoria |
| 4 | Cola de requests con prioridad + cancelación de renders obsoletos | ❌ No hay cancelación | Implementar cola con prioridad y cancel flag |
| 5 | Render async en hilo separado, resultado por señal a GUI | ⚠️ Tenemos ThreadPoolExecutor + QTimer.singleShot | Validar que la GUI nunca recibe updates desde otro hilo |
| 6 | **Tiles** cuando página > 4× pantalla | ❌ No existe | Render por tiles para zooms altos/escaneos |
| 7 | Cache por **bytes** con límite = RAM/3 y desalojo por prioridad | ⚠️ Cache fijo de 30 páginas | Cache con límite de memoria en MB |
| 8 | QAbstractScrollArea con scroll propio | ⚠️ Usamos QScrollArea contenedora | Migrar PDFViewer a QAbstractScrollArea |
| 9 | `scrollContentsBy()` no renderiza; render en evento de viewport | ⚠️ Renderizamos en el timer de scroll | Separar: scroll → solo pintar; viewport-change → pedir pixmaps |

---

## Referencias exactas (para volver a consultar)

| Tema | Archivo | Líneas aprox. |
|------|---------|---------------|
| Apertura sin render | `generators/poppler/generator_pdf.cpp` | `loadPages()` 880–935 |
| Requests por viewport | `part/pageview.cpp` | `requestVisiblePixmaps()` ~4950–5130 |
| Preload 512 px | `part/pageview.cpp` | 4970, 5077–5080 |
| Cola de requests | `core/document.cpp` | `sendGeneratorPixmapRequest()` 1324–1430 |
| Cancelación | `core/document.cpp` | 3169–3252 |
| Tiles switch-on | `core/document.cpp` | ~1404 |
| Threads de render | `core/generator_p.h/.cpp` | 109–146 / 56–60 |
| Cache memoria | `core/document.cpp` | 253–330 |
| Scroll handling | `part/pageview.cpp` | `scrollContentsBy()` 3509, QScroller 406 |
