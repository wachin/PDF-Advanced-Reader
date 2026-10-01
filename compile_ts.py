#!/usr/bin/env python3
"""
Script para compilar archivos .ts a .qm usando lrelease.

Usage:
    python3 compile_ts.py
"""

import os
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).parent
    ts_dir = root / "translations"
    
    if not ts_dir.exists():
        print(f"Error: {ts_dir} not found")
        sys.exit(1)
    
    # Try to find lrelease
    lrelease = shutil.which("lrelease")
    if not lrelease:
        # Try common locations
        for path in [
            "/usr/bin/lrelease",
            "/usr/local/bin/lrelease",
            shutil.which("pylrelease6"),
        ]:
            if path and os.path.exists(path):
                lrelease = path
                break
    
    if not lrelease:
        print("Error: lrelease not found. Install Qt6 linguist tools:")
        print("  sudo apt install qt6-l10n-tools")
        sys.exit(1)
    
    count = 0
    for ts_file in ts_dir.glob("*.ts"):
        qm_file = ts_file.with_suffix(".qm")
        print(f"Compiling {ts_file.name}...")
        try:
            subprocess.run([lrelease, str(ts_file)], check=True, capture_output=True)
            count += 1
        except subprocess.CalledProcessError as e:
            print(f"Error compiling {ts_file.name}: {e.stderr.decode()}")
    
    print(f"Compiled {count} translation file(s)")


if __name__ == "__main__":
    import shutil
    main()
