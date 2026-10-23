#!/usr/bin/env python3
"""Render the application headlessly and save a screenshot.

Usage:
    python3 tools/screenshot.py /path/to/file.pdf out.png [--page N] [--width W] [--height H]

Useful for visual regression checks and for verifying rendering on machines
without a display.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time

# Do not read or overwrite the user's PDFAR settings (saved view state would
# override --page and leak between runs).
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="pdfar-tool-"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from pdfar.main import MainWindow  # noqa: E402


def wait_until(app, predicate, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("out")
    ap.add_argument("--page", type=int, default=0)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=860)
    ap.add_argument("--search", default="", help="run a search and show highlights")
    ap.add_argument("--reflow", action="store_true", help="enable reflow mode")
    args = ap.parse_args()

    if not os.path.isfile(args.pdf):
        print("file not found:", args.pdf, file=sys.stderr)
        return 1

    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(args.width, args.height)
    win.show()
    app.processEvents()

    if not win.open_document(args.pdf):
        print("could not open", args.pdf, file=sys.stderr)
        return 1

    # Let the deferred restore of the saved view state run before we navigate,
    # otherwise it would override the requested page.
    app.processEvents()
    win._goto(args.page)
    if args.search:
        win.query_edit.setText(args.search)
        win.start_search()

    view = win.view

    if args.reflow:
        view.set_reflow_mode(True)
        win.act_reflow.setChecked(True)
        ok = wait_until(app, lambda: view._reflow_worker is None, timeout=120)
        if not ok:
            print("warning: reflow did not finish preparing", file=sys.stderr)
        win.status_left.setText(
            "Reflow ready" if view._reflow_has_text
            else "Reflow unavailable: no extractable text (scanned PDF?)")
    else:
        def visible_pages_ready():
            first, last = view.visible_range()
            return all(view.cache.get(p, view.zoom, view.rotation) is not None
                       for p in range(first, last + 1))

        if not wait_until(app, visible_pages_ready):
            print("warning: some visible pages did not finish rendering", file=sys.stderr)
    app.processEvents()

    win.grab().save(args.out)
    print(f"saved {args.out}  zoom={view.zoom:.2f} rot={view.rotation} page={view.current_page + 1}")
    win._close_document()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
