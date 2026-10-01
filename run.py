#!/usr/bin/env python3
"""Wrapper para ejecutar PDFAR como paquete."""

import sys
import os

# Agregar el directorio src al path
src_path = os.path.join(os.path.dirname(__file__), 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from pdfar.main import main

if __name__ == '__main__':
    main()
