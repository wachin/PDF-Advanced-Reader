# AGENTS.md — Guía para agentes de IA y contribuidores

## ⚠️ Importante: sobre `external/okular`

El directorio `external/okular/` **NO es una librería de este proyecto**.

- **NO se compila, NO se importa, NO se enlaza** con PDF-Advanced-Reader.
- **NO se modifican archivos** dentro de `external/okular/`.
- Es un **material de referencia exclusivamente para consulta y lectura**.

**Propósito:** Okular es un visor de PDF muy maduro (KDE). Este repositorio lo
incluye como código fuente de referencia para **estudiar e investigar**:

1. **Cómo Okular carga los documentos** — especialmente PDFs con páginas
   escaneadas (imágenes grandes y pesadas) que se abren rápido y se ven bien.
2. **Cómo Okular maneja el scroll** — desplazamiento fluido con renderizado
   de páginas bajo demanda.

El objetivo es que **PDF-Advanced-Reader** adopte las mismas técnicas de
carga de documentos y de manejo del scroll que Okular, pero implementadas
en Python/PyQt6/PyMuPDF.

### Reglas al consultar `external/okular/`

| Permitido | Prohibido |
|-----------|-----------|
| Leer el código fuente | Copiar código GPL sin respetar licencia* |
| Estudiar arquitectura y patrones | Compilarlo como dependencia |
| Documentar hallazgos en `docs/` | Importarlo desde `src/pdfar/` |
| Comparar enfoques con nuestro código | Modificar sus archivos |

\* Nota: Okular es GPL-2.0+/GPL-3.0+. PDF-Advanced-Reader es GPL-3.0+, por
lo que **inspirarse en sus técnicas y arquitectura es válido**; si algún día
se copiara código textual, debe respetarse la licencia y atribución. Las
ideas y algoritmos no tienen copyright — la implementación en
Python/PyQt6/PyMuPDF será siempre original.

### Archivos de Okular más relevantes para estudiar

Las conclusiones del análisis ya están documentadas en
**`docs/okular-architecture.md`** — consúltalo antes de re-investigar.

| Área | Ruta en `external/okular/` |
|------|---------------------------|
| Carga de documento, render async, cache | `core/document.cpp` / `core/document.h` |
| API de generadores (backend PDF) | `core/generator.h` |
| Modelo de página y tiles | `core/page.h` / `core/tile.h` |
| Backend PDF (Poppler) | `generators/poppler/generator_pdf.cpp` |
| Widget de scroll/vista de páginas | `part/pageview.cpp` / `part/part.cpp` |
| Configuración de memoria/cache | `conf/` |

---

## Estructura de este proyecto

```
PDF-Advanced-Reader/
├── src/pdfar/          # Código de la aplicación (Python/PyQt6/PyMuPDF)
├── tests/              # Tests pytest
├── external/okular/    # ⚠️ SOLO REFERENCIA — no es código del proyecto
├── debian/             # Empaquetado Debian
└── run.py              # Punto de entrada: python3 run.py
```

## Convenciones de trabajo

- Trabajar en incrementos pequeños; commit por tarea.
- Tests primero cuando sea posible (`PYTHONPATH=src python3 -m pytest tests/ -v`).
- No mezclar refactor grande con features nuevas.
- Ver `AGENT_TASK.md` para el plan maestro de fases y `ROADMAP.md` para el
  estado actual del roadmap.
