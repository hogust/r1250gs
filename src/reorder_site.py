#!/usr/bin/env python3
"""Move filters and result table to the top of the generated site."""

from __future__ import annotations

import argparse
from pathlib import Path

from bs4 import BeautifulSoup


def reorder(html_path: Path) -> None:
    html = html_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    meta = soup.select_one(".site-meta")
    filters = soup.select_one(".filter-wrap")
    results = soup.select_one(".results-wrap")

    if not meta:
        raise RuntimeError("Kunde inte hitta .site-meta i HTML-sidan")
    if not filters:
        raise RuntimeError("Kunde inte hitta .filter-wrap i HTML-sidan")
    if not results:
        raise RuntimeError("Kunde inte hitta .results-wrap i HTML-sidan")

    # detach first so they can safely be inserted at the new position
    filters.extract()
    results.extract()

    # Desired order: status/update time -> filters -> table -> remaining charts/history
    meta.insert_after(results)
    meta.insert_after(filters)

    html_path.write_text(str(soup), encoding="utf-8")
    print(f"Flyttade filter och resultattabell högst upp: {html_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", default="public/index.html")
    args = parser.parse_args()
    reorder(Path(args.html))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
