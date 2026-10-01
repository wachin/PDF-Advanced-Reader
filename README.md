# PDFAR - Lector de PDF Avanzado

Lector de PDF para Linux con búsqueda avanzada por página, zoom flexible y carga diferida.

## Características

- **Búsqueda avanzada**: AND, OR, frases exactas
- **Zoom flexible**: Desde 50% hasta 300%, con atajos de teclado
- **Carga diferida**: Renderizado de páginas bajo demanda para documentos grandes
- **Caché inteligente**: Memoriza páginas renderizadas para scroll fluido

## Instalación

### Dependencias

```bash
sudo apt install python3-pyqt6
pip3 install --user PyMuPDF
```

### Ejecutar

```bash
# Como paquete modular
python3 -m pdfar

# O desde el script raíz
python3 pdfar.py
```

## Arquitectura

```
src/pdfar/
├── __init__.py   # Metadata del paquete
├── main.py       # Ventana principal y UI
├── search.py     # Lógica de búsqueda
├── viewer.py     # Componentes de visualización
└── cache.py      # Caché de páginas renderizadas
```

## Licencia

GPL-3.0 - Ver LICENSE

## Autor

Washington Indacochea Delgado (linuxfrontier@proton.me)
