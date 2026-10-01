# Estructura Modular para Debian

## Estructura

```
PDF-Advanced-Reader/
├── debian/                    # Meta-datos Debian
│   ├── changelog              # Historial de versiones
│   └── copyright              # Información de licencia
├── src/pdfar/                 # Código fuente modular
│   ├── __init__.py
│   ├── main.py                # Ventana principal
│   ├── search.py              # Lógica de búsqueda
│   ├── viewer.py              # Componentes de visualización
│   └── cache.py               # Caché de renderizado
├── run.py                     # Entry point (wrapper)
├── pyproject.toml             # Configuración de paquete Python
├── README.md
└── LICENSE
```

## Requisitos Debian

Este proyecto sigue estas prácticas para futura inclusión en Debian:

1. **Nombre de paquete**: `pdfar` (lowercase, sin guiones bajos)
2. **Fuente**: Código en `src/` con estructura de paquete Python
3. **Dependencias**: 
   - `python3-pyqt6` (paquete Debian)
   - `python3-pymupdf` (paquete Debian - si disponible) o `pip`
4. **Licencia**: GPL-3.0+ (compatible con Debian)
5. **Archivos**: `debian/copyright` en formato DEP-5

## Próximos pasos

- [ ] Añadir `debian/control` con dependencias
- [ ] Crear `debian/rules` para deb_helper
- [ ] Añadir manpage
- [ ] Crear iconos en múltiples tamaños
- [ ] Documentación completa
