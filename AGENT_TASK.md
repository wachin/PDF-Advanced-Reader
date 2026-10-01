# TAREA MAESTRA — Convertir PDFAR en un lector de PDF profesional para Linux

**Rol del agente:** Eres un ingeniero de software senior especializado en
aplicaciones de escritorio Linux con Qt/PyQt6, con experiencia en motores de
renderizado de PDF (MuPDF/Poppler/PDFium), en arquitecturas multihilo
seguras para Qt, y en UX de lectores de PDF comerciales (Foxit Reader,
Okular, Evince). Tu código debe ser limpio, testeable, documentado y
empaquetable para Debian.

**Objetivo:** Transformar PDFAR (actualmente v0.1.0) en un lector de PDF
comparable a Foxit Reader en features esenciales, con énfasis en:
rendimiento asíncrono, navegación fluida, búsqueda avanzada y empaquetado
profesional. No introduzcas dependencias nuevas sin justificarlo en
`ARCHITECTURE.md`.

---

## 1. BUGS CRÍTICOS A CORREGIR PRIMERO (Fase 0 — no negociable)

### 1.1. Solo se renderizan las primeras 10 páginas
**Síntoma reportado:** Con un PDF de 108 páginas, al hacer scroll solo se ven
las páginas 1–10; el resto queda con placeholder "Page N".

**Causa raíz (ya diagnosticada):**
- `PDFViewer._get_visible_page_range()` no puede leer la posición real del
  scroll porque `PDFViewer` es hijo del viewport del `QScrollArea`, no del
  `QScrollArea`. `hasattr(parent, 'verticalScrollBar')` siempre es `False`.
- `self.height()` devuelve la altura TOTAL del widget (todas las páginas),
  no la altura visible del viewport → todas las páginas se consideran
  visibles → `first=last=107`.
- `_on_scroll()` calcula mal `before_range/after_range` y solo encola
  páginas cercanas al final.

**Solución esperada (arquitectura):**
- Refactorizar para que `PDFViewer` sea un `QWidget` que **NO** dependa de
  `QScrollArea`. Implementar el scroll internamente con
  `QAbstractScrollArea` + `QScrollBar` propio, o bien exponer una API
  explícita: `set_scroll_position(y)` / `visible_rect()`.
- **Recomendado:** reescribir el visor como `QAbstractScrollArea` con
  viewport propio. Esto es lo que hacen Okular/Foxit y elimina toda
  ambigüedad de "¿quién es mi padre?".
- `_get_visible_page_range()` debe calcularse desde `self.viewport().rect()`
  y la posición de la barra de scroll **propia**, usando un mapa acumulativo
  de alturas (cachear `page_tops[i]` para no recalcular en cada scroll).
- Al hacer scroll, encolar renderizado de páginas dentro de:
  `[first - PREFETCH_BACK, last + PREFETCH_FORWARD]`.
  Sugerido: `PREFETCH_BACK = 2`, `PREFETCH_FORWARD = 8`.
- Cancelar renders de páginas que salieron del viewport hace más de N ms
  (evita saturar el pool cuando el usuario hace scroll rápido).

### 1.2. `SearchWorker.run()` usa `re` sin importar
- Añadir `import re` en `main.py`.
- Además, `whole_words` no se usa actualmente. Implementarlo con
  `re.compile(r'\b' + re.escape(term) + r'\b')`.
- Añadir tests en `tests/test_search.py` para AND, OR, PHRASE, whole_words,
  case_sensitive y proximidad.

### 1.3. Ciclo de vida de workers frágil
- Reemplazar `concurrent.futures.ThreadPoolExecutor` + `QTimer` de polling
  por `QThreadPool` + `QRunnable` con señales `pyqtSignal`. El worker debe
  emitir `finished(page_index, QImage, error)` y conectarse al hilo GUI.
- Cancelación real: `QRunnable` con flag `_cancelled` chequeado antes y
  después del render.

### 1.4. Fugas al abrir un segundo PDF
- `open_pdf()` debe: cancelar workers activos, limpiar cache, hacer
  `viewer.deleteLater()`, desconectar señales del scrollbar anterior,
  resetear `search_rects_per_page`, y crear el nuevo viewer.
- Añadir test que abra 3 PDFs seguidos y verifique `len(active_workers)==0`
  y que el objeto viewer anterior fue destruido.

---

## 2. FASE 1 — Navegación y renderizado de calidad profesional

- **Scroll continuo con snap opcional** a inicio de página.
- **Barra de estado** con: página actual / total, zoom actual, modo de
  ajuste (Fit Page / Fit Width / Custom).
- **Ir a página:** diálogo `Ctrl+G`.
- **Historial atrás/adelante** (`Alt+Left`, `Alt+Right`).
- **Zoom:**
  - `Ctrl+rueda` → zoom incremental centrado en el cursor.
  - Presets: 25, 50, 75, 100, 125, 150, 200, 300, 400, 550 %.
  - Modos persistentes: Fit Page, Fit Width, Fit Height, Actual Size.
  - Re-render con **zoom por tile** para zooms > 300 % (evita bitmaps de
    más de 20 000 px).
- **Selección de texto** con `page.get_text("words")` + `QTextCursor`
  sobre un `QGraphicsView` o widget custom. Copy (Ctrl+C), Select All.
- **Búsqueda incremental** (mientras se escribe, con debounce de 250 ms).
- **Navegación por resultados:** F3 / Shift+F3, resaltado del hit actual
  en color distinto (naranja para el activo, amarillo para el resto).
- **Sidebar de miniaturas** (`QDockWidget` izquierdo) con carga asíncrona
  y lazy, ancho ajustable.
- **Índice / TOC (bookmarks)** en dock izquierdo con pestañas
  (Miniaturas | Índice | Anotaciones | Búsqueda).
- **Atajos estándar** (documentar en `SHORTCUTS.md`):
  `Ctrl+O, Ctrl+W, Ctrl+Q, Ctrl+F, F3, Shift+F3, Ctrl+G, Ctrl+±, Ctrl+0,
   Ctrl+1, Ctrl+2, Inicio, Fin, AvPág, RePág, Espacio, Shift+Espacio`.

## 3. FASE 2 — Features "Foxit-level"

- **Anotaciones persistentes**: resaltado, subrayado, tachado, nota
  adhesiva, lápiz, rectángulo. Guardar en el PDF con `page.add_highlight_annot`,
  `add_underline_annot`, `add_strikeout_annot`, `add_text_annot`,
  `add_ink_annot`, `add_rect_annot`. Serializar y reabrir.
- **Modo presentación** (F5): página completa, sin toolbar, avance con
  clic/espacio.
- **Modo oscuro** con inversión de color de página (`QImage.invertPixels`
  con opción de preservar imágenes) y respeto al tema del sistema.
- **Vista de dos páginas** y **vista continua** (toggle).
- **Imprimir** con `QtPrintSupport` (rango, escala, orientación).
- **Exportar** página actual como PNG/JPG y texto como TXT.
- **Marcadores de usuario** (bookmarks propios, no los del PDF).
- **Comparar** ya no — fuera de scope de v1.
- **Formularios PDF** (AcroForm): renderizar y rellenar campos con
  `page.widgets()` de PyMuPDF. Guardar con `doc.save(..., incremental=True)`.
- **Firma digital** de solo lectura: mostrar panel con validez de firma.

## 4. FASE 3 — Rendimiento y empaquetado

- **Cache multinivel:**
  - L1: bitmaps recientes (`PageCache` actual, 30–50 páginas).
  - L2: texturas en disco en `~/.cache/pdfar/<hash>.png` con LRU de 500 MB.
  - Precarga predictiva basada en dirección del scroll.
- **Renderizado por tiles** para zooms altos y páginas grandes.
- **Thread pool** con `QThreadPool` y `maxThreadCount = min(8, ncpu)`.
- **Perfilado**: incluir script `tools/profile_open.py` que mida tiempo de
  apertura, primer render y memoria pico con `tracemalloc`.
- **Empaquetado Debian completo** (ver `DEBIAN.md`):
  - `debian/control`, `debian/rules` (dh-python), `debian/changelog`,
    `debian/copyright` (DEP-5), `debian/install`, `debian/pdfar.1`
    (manpage), iconos SVG + PNG 16/32/64/128/256 en
    `/usr/share/icons/hicolor/`.
  - `.desktop` file con `MimeType=application/pdf;`.
  - Build reproducible: `SOURCE_DATE_EPOCH`, `dh --no-parallel` si aplica.
  - Probar con `lintian -iIE --pedantic pdfar_*.changes`.
- **Flatpak opcional** en `flatpak/org.pdfar.PDFAR.yml`.
- **AppImage** con `linuxdeploy` en `tools/build_appimage.sh`.

## 5. FASE 4 — Calidad de código

- **Tests** con `pytest` + `pytest-qt`:
  - `tests/test_cache.py` (LRU, invalidación por zoom, concurrencia).
  - `tests/test_search.py` (todos los modos, whole_words, unicode).
  - `tests/test_viewer.py` (geometría, rango visible, goto_page,
    cancelación de workers).
  - `tests/test_i18n.py` (carga en/es, fallback).
  - `tests/test_open_cycles.py` (abrir/cerrar 5 PDFs sin leaks).
  - Fixture que genere PDFs sintéticos de 1, 100, 1000 páginas.
- **CI** en `.github/workflows/ci.yml`: ruff, mypy, pytest en Ubuntu 22.04
  y 24.04, con `xvfb-run`.
- **Tipado estricto** (`mypy --strict` en `src/pdfar/`).
- **Formato**: `ruff format` + `ruff check`.
- **Docstrings** estilo Google en todos los módulos públicos.
- **Logging** con `logging` (niveles configurables por
  `PDFAR_LOG=debug`), nunca `print()`.
- **Manejo de errores:** ningún `except Exception: pass`. Usar
  `QMessageBox` para errores de usuario y `logging.exception` para bugs.

## 6. FASE 5 — i18n y accesibilidad

- Completar `pdfar_en.ts` y `pdfar_es.ts` cubriendo **todo** string
  visible (toolbar, menús, diálogos, mensajes de estado, tooltips).
- Extraer strings con `pyside6-lupdate` (o `pylupdate6`) automatizado en
  `Makefile` (`make i18n`).
- Añadir `de`, `fr`, `pt`, `it` como plantillas vacías.
- Accesibilidad: nombres accesibles en widgets, orden de tabulación,
  contraste AA, soporte para lectores de pantalla (Qt lo da casi gratis si
  se usan widgets estándar en vez de `QLabel` custom).

---

## 7. Criterios de aceptación (Definition of Done)

Un PR se considera completo cuando:

1. ✅ Abre un PDF de 108 páginas y **se puede hacer scroll hasta la última
   página**, con render asíncrono sin bloquear la GUI.
2. ✅ Al soltar el scroll, en < 500 ms se ven las páginas del viewport y
   las 8 siguientes pre-renderizadas.
3. ✅ Zoom del 50 % al 550 % mantiene fluidez y no produce bitmaps de
   memoria desproporcionada (usar tiles > 300 %).
4. ✅ Búsqueda AND/OR/PHRASE con resaltado amarillo y hit activo naranja;
   navegación F3/Shift+F3.
5. ✅ Abrir un segundo PDF no deja workers huérfanos ni memoria retenida.
6. ✅ `pytest -q` pasa al 100 %, cobertura ≥ 70 % en `src/pdfar/`.
7. ✅ `mypy --strict src/pdfar` sin errores.
8. ✅ `lintian -iIE --pedantic` sin errores (warnings aceptables si están
   justificados en `debian/README.source`).
9. ✅ `dpkg-buildpackage -b -us -uc` produce un `.deb` instalable.
10. ✅ Ejecutable en Ubuntu 22.04, 24.04 y Debian 12 (probar en contenedores).

---

## 8. Metodología de trabajo del agente

- Trabaja por **ramas**: `fix/scroll-render`, `feat/annotations`,
  `feat/packaging`, etc. Un PR por fase.
- **No mezcles** refactor grande con nuevas features en el mismo commit.
- Antes de cada fase: lee el código actual, escribe un breve `DESIGN.md`
  con el plan, pide confirmación si algo es ambiguo, y solo entonces
  implementa.
- Después de cada fase: actualiza `CHANGELOG.md` (formato Keep a Changelog),
  `README.md` (features) y añade/actualiza tests.
- Nunca elimines funcionalidad existente sin justificar en el PR.
- Prioriza siempre: **correctitud > rendimiento > elegancia**.

## 9. Stack y dependencias permitidas

- Python ≥ 3.10
- PyQt6 ≥ 6.4
- PyMuPDF ≥ 1.24
- Opcional (justificar): `pytest`, `pytest-qt`, `ruff`, `mypy`, `pillow`
  (solo para export), `send2trash` (para "Recycle").
- **Prohibido**: dependencias abandonadas, GPL-incompatibles, o que
  dupliquen funcionalidad de PyMuPDF.

## 10. Entregables

- `src/pdfar/` refactorizado.
- `tests/` completo.
- `debian/` completo.
- `docs/ARCHITECTURE.md`, `docs/SHORTCUTS.md`, `docs/BUILD.md`.
- `CHANGELOG.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`.
- `LICENSE` (GPL-3.0 ya existe — mantener).
- `Makefile` con targets: `install`, `test`, `lint`, `typecheck`, `i18n`,
  `deb`, `clean`.

Empieza por la **Fase 0** completa (bugs 1.1–1.4) con tests que
reproduzcan el bug antes de arreglarlo. Reporta al final de cada fase con
un resumen de cambios, archivos tocados y evidencia (salida de tests,
captura o log).

---

**Nota final para el agente:** si en algún momento tienes dudas sobre
prioridades, vuelve a leer la sección 7 (Definition of Done). El objetivo
no es tener más features, sino que las que existan funcionen de forma
impecable, como en Foxit Reader.
