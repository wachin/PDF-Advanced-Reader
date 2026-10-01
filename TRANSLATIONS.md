# Internacionalización (i18n)

PDFAR usa **Qt Linguist** para soporte multi-idioma.

## Estructura

```
translations/
├── pdfar_en.ts    # Traducción inglesa (fuente)
├── pdfar_es.ts    # Traducción española
├── pdfar_en.qm    # Archivo binario compilado (autogenerado)
└── pdfar_es.qm    # Archivo binario compilado (autogenerado)
```

## Flujo de trabajo

### 1. Añadir texto traducible

En código Python, usa strings en inglés (ya está implementado):

```python
# Ventana principal
self.setWindowTitle("PDFAR - Advanced PDF Reader")

# Botones
btn_search = QPushButton("Search")

# Mensajes
QMessageBox.information(self, "No results", "No results found.")
```

### 2. Compilar traducciones

```bash
# Compilar .ts → .qm
python3 compile_ts.py
```

### 3. Añadir nueva traducción

1. Copia un archivo .ts existente
2. Edita con Qt Linguist (`linguist pdfar_de.ts`) o encriptador
3. Compila: `python3 compile_ts.py`

### 4. Usar Qt Linguist (opcional)

```bash
# Editar traducciones
linguist translations/pdfar_es.ts

# Ver texto no traducido
linguist translations/pdfar_es.ts -ts
```

## Código de idiomas soportados

- `en` — English (predeterminado)
- `es` — Español
- `de` — Deutsch
- `fr` — Français
- `pt` — Português
- `it` — Italiano

Más idiomas: añade `pdfar_xx.ts` y compila.

## Detección automática

La app carga el idioma del sistema:

```python
# En i18n.py
i18n.load_translations()  # Usa QLocale.system()
```

## Variables de entorno

Forzar idioma (antes de ejecutar):

```bash
export LC_ALL=es_ES.UTF-8
python3 run.py
```

## Nuevas cadenas

1. Añadir al código: `btn.setText("Nuevo texto")`
2. Extraer con: `pyside6-lupdate . -ts translations/pdfar_es.ts`
3. Editar: `linguist translations/pdfar_es.ts`
4. Compilar: `python3 compile_ts.py`
