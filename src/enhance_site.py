#!/usr/bin/env python3
"""Enhance the generated R1250GS HTML site with filters, freshness metadata and history."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px

HISTORY_COLUMNS = [
    "Datum",
    "Modelltyp",
    "Antal annonser",
    "Medianpris (kr)",
    "Medelpris (kr)",
    "Median miltal",
    "Median årsmodell",
]


def update_history(df: pd.DataFrame, history_path: Path, today: str) -> pd.DataFrame:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    if history_path.exists():
        history = pd.read_csv(history_path)
    else:
        history = pd.DataFrame(columns=HISTORY_COLUMNS)

    if not history.empty and "Datum" in history.columns:
        history = history[history["Datum"].astype(str) != today].copy()

    rows = []
    for variant in ("GS", "GSA"):
        part = df[df["Modelltyp"].astype(str) == variant].copy()
        part["Pris (kr)"] = pd.to_numeric(part["Pris (kr)"], errors="coerce")
        part["Miltal"] = pd.to_numeric(part["Miltal"], errors="coerce")
        part["Årsmodell"] = pd.to_numeric(part["Årsmodell"], errors="coerce")
        part = part.dropna(subset=["Pris (kr)"])
        if part.empty:
            continue
        rows.append(
            {
                "Datum": today,
                "Modelltyp": variant,
                "Antal annonser": int(len(part)),
                "Medianpris (kr)": round(float(part["Pris (kr)"].median())),
                "Medelpris (kr)": round(float(part["Pris (kr)"].mean())),
                "Median miltal": round(float(part["Miltal"].median())) if part["Miltal"].notna().any() else None,
                "Median årsmodell": round(float(part["Årsmodell"].median()), 1) if part["Årsmodell"].notna().any() else None,
            }
        )

    if rows:
        history = pd.concat([history, pd.DataFrame(rows)], ignore_index=True)

    history = history[HISTORY_COLUMNS].sort_values(["Datum", "Modelltyp"])
    history.to_csv(history_path, index=False, encoding="utf-8")
    return history


def history_html(history: pd.DataFrame) -> str:
    if history.empty:
        return "<p>Historiken börjar byggas från och med den här körningen.</p>"

    plot_df = history.copy()
    plot_df["Datum"] = pd.to_datetime(plot_df["Datum"], errors="coerce")
    plot_df["Medianpris (kr)"] = pd.to_numeric(plot_df["Medianpris (kr)"], errors="coerce")
    plot_df["Antal annonser"] = pd.to_numeric(plot_df["Antal annonser"], errors="coerce")
    plot_df = plot_df.dropna(subset=["Datum", "Medianpris (kr)"])

    fig = px.line(
        plot_df,
        x="Datum",
        y="Medianpris (kr)",
        color="Modelltyp",
        markers=True,
        hover_data=["Antal annonser"],
        title="Prisnivå över tid – median av aktuella utropspriser",
    )
    fig.update_layout(
        xaxis_title="Datum",
        yaxis_title="Medianpris (kr)",
        hovermode="x unified",
        margin=dict(l=40, r=20, t=70, b=40),
        height=480,
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


def enhance_html(html_path: Path, history: pd.DataFrame, updated_at: str) -> None:
    html = html_path.read_text(encoding="utf-8")

    extra_css = r"""
    <style>
      .site-meta { margin: 16px 18px 0; padding: 10px 14px; background: #f5f7fa; border-radius: 8px; color: #333; }
      .history-wrap, .filter-wrap { padding: 0 18px; margin: 28px auto; }
      .filters { display: flex; flex-wrap: wrap; gap: 10px; align-items: end; padding: 14px; border: 1px solid #ddd; border-radius: 8px; background: #fafafa; }
      .filters label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; font-weight: 600; }
      .filters input, .filters select, .filters button { font: inherit; padding: 8px 10px; border: 1px solid #bbb; border-radius: 6px; background: white; }
      .filters button { cursor: pointer; font-weight: 600; }
      .filter-count { margin: 10px 0 0; color: #444; }
      table.result-table a { display: inline-block; padding: 5px 8px; border: 1px solid #0b57d0; border-radius: 5px; }
      table.result-table td a.ad-title-link { padding: 0; border: 0; font-weight: 600; }
      @media (max-width: 700px) {
        .filters { display: grid; grid-template-columns: 1fr 1fr; }
        .filters label.search-wide { grid-column: 1 / -1; }
      }
    </style>
    """

    meta = f'<div class="site-meta"><b>Senast uppdaterad:</b> {updated_at} &nbsp;·&nbsp; Automatisk Blocket-analys</div>'

    hist_section = f"""
    <div class="history-wrap">
      <h2>Prisutveckling</h2>
      <p>Historiken visar medianen av annonsernas aktuella utropspriser för GS respektive GSA vid varje körning.</p>
      {history_html(history)}
    </div>
    <hr style="margin:40px 0">
    """

    filters = r"""
    <div class="filter-wrap">
      <h2>Filtrera annonser</h2>
      <div class="filters">
        <label>Modell
          <select id="filter-variant">
            <option value="">Alla</option>
            <option value="GS">GS</option>
            <option value="GSA">GSA</option>
          </select>
        </label>
        <label>Årsmodell min<input id="filter-year-min" type="number" inputmode="numeric"></label>
        <label>Årsmodell max<input id="filter-year-max" type="number" inputmode="numeric"></label>
        <label>Pris min<input id="filter-price-min" type="number" inputmode="numeric" step="1000"></label>
        <label>Pris max<input id="filter-price-max" type="number" inputmode="numeric" step="1000"></label>
        <label>Miltal max<input id="filter-mileage-max" type="number" inputmode="numeric" step="100"></label>
        <label class="search-wide">Sök<input id="filter-search" type="search" placeholder="rubrik, ort, säljare…"></label>
        <button id="filter-reset" type="button">Nollställ</button>
      </div>
      <p class="filter-count" id="filter-count"></p>
    </div>
    """

    script = r"""
    <script>
    document.addEventListener("DOMContentLoaded", () => {
      const table = document.querySelector("table.result-table");
      if (!table) return;

      const headers = Array.from(table.querySelectorAll("thead th")).map(th => th.textContent.trim());
      const idx = name => headers.indexOf(name);
      const cols = {
        variant: idx("Modelltyp"),
        year: idx("Årsmodell"),
        price: idx("Pris (kr)"),
        mileage: idx("Miltal"),
        title: idx("Annonsrubrik"),
        link: idx("Direktlänk")
      };

      const rows = Array.from(table.querySelectorAll("tbody tr"));
      const value = (row, col) => col >= 0 ? row.children[col].textContent.trim() : "";
      const numberValue = (row, col) => {
        const cleaned = value(row, col).replace(/[^\d.,-]/g, "").replace(",", ".");
        const n = Number(cleaned);
        return Number.isFinite(n) ? n : null;
      };

      rows.forEach(row => {
        if (cols.link >= 0) {
          const a = row.children[cols.link].querySelector("a");
          if (a) {
            a.textContent = "Öppna på Blocket ↗";
            if (cols.title >= 0 && !row.children[cols.title].querySelector("a")) {
              const title = row.children[cols.title].textContent.trim();
              if (title) {
                const titleLink = document.createElement("a");
                titleLink.href = a.href;
                titleLink.target = "_blank";
                titleLink.rel = "noopener";
                titleLink.className = "ad-title-link";
                titleLink.textContent = title;
                row.children[cols.title].textContent = "";
                row.children[cols.title].appendChild(titleLink);
              }
            }
          }
        }
      });

      const controls = {
        variant: document.getElementById("filter-variant"),
        yearMin: document.getElementById("filter-year-min"),
        yearMax: document.getElementById("filter-year-max"),
        priceMin: document.getElementById("filter-price-min"),
        priceMax: document.getElementById("filter-price-max"),
        mileageMax: document.getElementById("filter-mileage-max"),
        search: document.getElementById("filter-search"),
        count: document.getElementById("filter-count"),
        reset: document.getElementById("filter-reset")
      };

      const optionalNum = el => el.value === "" ? null : Number(el.value);
      const apply = () => {
        const variant = controls.variant.value;
        const yearMin = optionalNum(controls.yearMin);
        const yearMax = optionalNum(controls.yearMax);
        const priceMin = optionalNum(controls.priceMin);
        const priceMax = optionalNum(controls.priceMax);
        const mileageMax = optionalNum(controls.mileageMax);
        const search = controls.search.value.trim().toLowerCase();
        let visible = 0;

        rows.forEach(row => {
          const year = numberValue(row, cols.year);
          const price = numberValue(row, cols.price);
          const mileage = numberValue(row, cols.mileage);
          const matches =
            (!variant || value(row, cols.variant) === variant) &&
            (yearMin === null || (year !== null && year >= yearMin)) &&
            (yearMax === null || (year !== null && year <= yearMax)) &&
            (priceMin === null || (price !== null && price >= priceMin)) &&
            (priceMax === null || (price !== null && price <= priceMax)) &&
            (mileageMax === null || (mileage !== null && mileage <= mileageMax)) &&
            (!search || row.textContent.toLowerCase().includes(search));
          row.style.display = matches ? "" : "none";
          if (matches) visible++;
        });
        controls.count.textContent = `${visible} av ${rows.length} annonser visas`;
      };

      [controls.variant, controls.yearMin, controls.yearMax, controls.priceMin,
       controls.priceMax, controls.mileageMax, controls.search].forEach(el => {
        el.addEventListener("input", apply);
        el.addEventListener("change", apply);
      });
      controls.reset.addEventListener("click", () => {
        [controls.variant, controls.yearMin, controls.yearMax, controls.priceMin,
         controls.priceMax, controls.mileageMax, controls.search].forEach(el => el.value = "");
        apply();
      });
      apply();
    });
    </script>
    """

    html = html.replace("</head>", extra_css + "</head>", 1)
    html = html.replace("<body>", "<body>" + meta, 1)
    marker = '<div class="results-wrap"><h2>Resultattabell</h2>'
    if marker in html:
        html = html.replace(marker, hist_section + filters + marker, 1)
    else:
        html = html.replace("</body>", hist_section + filters + "</body>", 1)
    html = html.replace("</body>", script + "</body>", 1)
    html_path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="public/r1250gs_blocket.csv")
    parser.add_argument("--html", default="public/index.html")
    parser.add_argument("--history", default="data/market_history.csv")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    html_path = Path(args.html)
    history_path = Path(args.history)

    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    now = datetime.now(ZoneInfo("Europe/Stockholm"))
    history = update_history(df, history_path, now.date().isoformat())
    enhance_html(html_path, history, now.strftime("%Y-%m-%d %H:%M %Z"))
    print(f"Förbättrad webbsida: {html_path}")
    print(f"Historik uppdaterad: {history_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
