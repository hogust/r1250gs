#!/usr/bin/env python3
"""Hämta och analysera BMW R1250GS/GSA-annonser från Blocket."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
from bs4 import BeautifulSoup
from blocket_api import BlocketAPI, McAd
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import PolynomialFeatures

QUERY = "BMW R 1250 GS"
INCLUDE_RE = re.compile(
    r"\b(?:BMW\s+)?(?:MOTORRAD\s+)?R?\s*1250\s*GS(?:A|[\s-]*ADVENTURE)?\b",
    re.IGNORECASE,
)
EXCLUDE_RE = re.compile(
    r"\bR?\s*1300\s*GS\b|\bR?\s*1200\s*GS\b|\b1250\s*(?:R|RS|RT)\b",
    re.IGNORECASE,
)

SWEDISH_MONTHS = (
    "januari|februari|mars|april|maj|juni|juli|augusti|"
    "september|oktober|november|december"
)
MONTH_MAP = {
    "januari": 1,
    "februari": 2,
    "mars": 3,
    "april": 4,
    "maj": 5,
    "juni": 6,
    "juli": 7,
    "augusti": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value).strip()


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, (dict, list)):
                out.update(flatten(value, path))
            else:
                out[path] = value
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            path = f"{prefix}[{i}]"
            if isinstance(value, (dict, list)):
                out.update(flatten(value, path))
            else:
                out[path] = value
    return out


def find_value(obj: Any, candidates: list[str]) -> Any:
    flat = flatten(obj)
    wanted = {name.lower() for name in candidates}
    for key, value in flat.items():
        last = re.sub(r"\[\d+\]$", "", key.split(".")[-1]).lower()
        if last in wanted and value not in (None, "", []):
            return value
    for key, value in flat.items():
        lower = key.lower()
        if any(name in lower for name in wanted) and value not in (None, "", []):
            return value
    return None


def find_int(obj: Any, candidates: list[str]) -> int | None:
    value = find_value(obj, candidates)
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"-?\d[\d\s\xa0.,]*", normalize_text(value))
    if not match:
        return None
    raw = re.sub(r"[^\d-]", "", match.group(0))
    try:
        return int(raw)
    except ValueError:
        return None


def find_title(obj: Any) -> str:
    return normalize_text(
        find_value(obj, ["heading", "title", "subject", "name", "ad_title", "display_name"])
    )


def find_id(obj: Any) -> int | None:
    value = find_value(obj, ["id", "ad_id", "adid", "listing_id", "item_id", "object_id"])
    if value is None:
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"\d{5,}", normalize_text(value))
    return int(match.group(0)) if match else None


def find_url(obj: Any, ad_id: int | None) -> str:
    value = find_value(obj, ["url", "web_url", "canonical_url", "share_url", "href", "link"])
    text = normalize_text(value)
    if text.startswith("http"):
        return text
    if text.startswith("/"):
        return "https://www.blocket.se" + text
    return f"https://www.blocket.se/mobility/item/{ad_id}" if ad_id else ""


def looks_like_r1250gs(obj: Any) -> bool:
    text = normalize_text(obj).replace("-", " ")
    return bool(INCLUDE_RE.search(text)) and not bool(EXCLUDE_RE.search(text))


def collect_dict_candidates(obj: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if find_id(value) is not None and find_title(value) and looks_like_r1250gs(value):
                found.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(obj)
    deduped: dict[int, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    for item in found:
        ad_id = find_id(item)
        if ad_id is None:
            anonymous.append(item)
        else:
            old = deduped.get(ad_id)
            if old is None or len(flatten(item)) > len(flatten(old)):
                deduped[ad_id] = item
    return list(deduped.values()) + anonymous


def seller_type_from(obj: Any) -> str:
    value = normalize_text(
        find_value(obj, ["seller_type", "dealer_type", "organization_type", "account_type"])
    ).lower()
    whole = normalize_text(obj).lower()
    if any(x in value for x in ("dealer", "company", "business", "professional")):
        return "Företag"
    if any(x in value for x in ("private", "person")):
        return "Privat"
    if any(x in whole for x in ('"dealer"', '"company"', '"business"', "företag")):
        return "Företag"
    if any(x in whole for x in ('"private"', "privat")):
        return "Privat"
    return ""


def classify_variant(obj: Any) -> str:
    text = normalize_text(obj).upper().replace("-", " ")
    if re.search(r"\bGSA\b|\b1250\s*GSA\b|\bGS\s*ADVENTURE\b|\bADVENTURE\b", text):
        return "GSA"
    return "GS"


def normalize_ad_date(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    match = re.fullmatch(
        r"(\d{1,2})\s+([A-Za-zÅÄÖåäö]+)\s+(\d{4})(?:,\s*(\d{1,2}):(\d{2}))?",
        value,
    )
    if not match:
        return value
    day = int(match.group(1))
    month = MONTH_MAP.get(match.group(2).lower())
    year = int(match.group(3))
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    if month is None:
        return value
    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"


def fetch_updated_date(url: str, timeout: float = 12.0) -> str:
    if not url:
        return ""
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    }
    try:
        response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Varning: kunde inte läsa annonsdatum från {url}: {exc}", file=sys.stderr)
        return ""

    page_text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
    match = re.search(
        rf"\bUppdaterad\s+(\d{{1,2}}\s+(?:{SWEDISH_MONTHS})\s+\d{{4}},\s+\d{{1,2}}:\d{{2}})",
        page_text,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    match = re.search(
        rf"\bUppdaterad\s+(\d{{1,2}}\s+(?:{SWEDISH_MONTHS})\s+\d{{4}})",
        page_text,
        re.IGNORECASE,
    )
    return match.group(1) if match else ""


def make_row(summary: dict[str, Any], details: dict[str, Any] | None) -> dict[str, Any]:
    merged = {"summary": summary, "details": details or {}}
    source = details if details else summary
    ad_id = find_id(summary) or find_id(source)
    return {
        "Annons-ID": ad_id or "",
        "Annonsdatum": "",
        "Annonsrubrik": find_title(source) or find_title(summary),
        "Modelltyp": classify_variant(merged),
        "Årsmodell": find_int(merged, ["year", "model_year", "registration_year", "year_model", "modelyear"]) or "",
        "Miltal": find_int(merged, ["mileage", "milage", "mileage_mil", "odometer"]) or "",
        "Pris (kr)": find_int(merged, ["price", "amount", "price_value", "sales_price"]) or "",
        "Säljare": normalize_text(
            find_value(merged, ["seller_name", "dealer_name", "organization_name", "company_name", "store_name", "seller"])
        ),
        "Säljartyp": seller_type_from(merged),
        "Ort": normalize_text(find_value(merged, ["location", "city", "municipality", "area", "region", "address"])),
        "Motorvolym (cc)": find_int(merged, ["engine_volume", "engine_volume_cc", "cylinder_volume", "displacement"]) or "",
        "Effekt (hk)": find_int(merged, ["horsepower", "hp", "effect", "power_hp"]) or "",
        "Regnr": normalize_text(find_value(merged, ["registration_number", "registration", "reg_number", "regno", "license_plate"])),
        "Chassinummer": normalize_text(
            find_value(merged, ["Chassinummer", "chassinummer", "vin", "VIN", "chassis_number", "vehicle_identification_number"])
        ),
        "Direktlänk": find_url(source, ad_id),
        "Beskrivning": normalize_text(find_value(merged, ["description", "body", "text", "ad_text"])),
    }


def add_market_analysis(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    for col in ["Årsmodell", "Miltal", "Pris (kr)"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["Förväntat marknadspris (kr)"] = pd.NA
    df["Prisavvikelse (kr)"] = pd.NA
    df["Prisavvikelse (%)"] = pd.NA
    models: dict[str, object] = {}

    for variant in ["GS", "GSA"]:
        mask = (
            (df["Modelltyp"] == variant)
            & df["Årsmodell"].notna()
            & df["Miltal"].notna()
            & df["Pris (kr)"].notna()
        )
        subset = df.loc[mask].copy()
        print(f"{variant}: {len(subset)} kompletta annonser för prisanalys.")
        if len(subset) < 8:
            print(f"  För få {variant}-annonser för separat regression ({len(subset)} st).")
            continue

        x = subset[["Årsmodell", "Miltal"]]
        y = subset["Pris (kr)"]
        model = make_pipeline(
            PolynomialFeatures(degree=2, include_bias=False),
            LinearRegression(),
        )
        model.fit(x, y)
        models[variant] = model
        df.loc[mask, "Förväntat marknadspris (kr)"] = model.predict(x).clip(min=0)

    expected = pd.to_numeric(df["Förväntat marknadspris (kr)"], errors="coerce")
    price = pd.to_numeric(df["Pris (kr)"], errors="coerce")
    df["Prisavvikelse (kr)"] = price - expected
    df["Prisavvikelse (%)"] = 100 * df["Prisavvikelse (kr)"] / expected
    return df, models


def create_plotly_dashboard(df: pd.DataFrame, html_path: str, models: dict[str, object]) -> None:
    usable = df.dropna(subset=["Årsmodell", "Miltal", "Pris (kr)", "Modelltyp"]).copy()
    if usable.empty:
        print("Ingen komplett data för graf. Hoppar över HTML.")
        return

    marker_symbols = {"GS": "circle", "GSA": "diamond"}
    marker_colors = {"GS": "#1f77b4", "GSA": "#d62728"}

    def add_variant(fig: go.Figure, variant: str) -> None:
        part = usable[usable["Modelltyp"] == variant].copy()
        if part.empty:
            return

        custom_cols = [
            "Annonsrubrik",
            "Säljare",
            "Ort",
            "Direktlänk",
            "Förväntat marknadspris (kr)",
            "Prisavvikelse (%)",
            "Annonsdatum",
        ]
        for col in custom_cols:
            if col not in part.columns:
                part[col] = ""

        fig.add_trace(
            go.Scatter3d(
                x=part["Årsmodell"],
                y=part["Miltal"],
                z=part["Pris (kr)"],
                mode="markers",
                name=f"{variant} annonser",
                marker=dict(
                    size=6,
                    symbol=marker_symbols[variant],
                    color=marker_colors[variant],
                    opacity=0.9,
                ),
                customdata=part[custom_cols].to_numpy(),
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    f"Modelltyp: {variant}<br>"
                    "Årsmodell: %{x:.0f}<br>"
                    "Miltal: %{y:.0f} mil<br>"
                    "Pris: %{z:,.0f} kr<br>"
                    "Förväntat: %{customdata[4]:,.0f} kr<br>"
                    "Avvikelse: %{customdata[5]:+.1f}%<br>"
                    "Annonsdatum: %{customdata[6]}<br>"
                    "Säljare: %{customdata[1]}<br>"
                    "Ort: %{customdata[2]}<br>"
                    "%{customdata[3]}<extra></extra>"
                ),
            )
        )

        if variant not in models:
            return

        model = models[variant]
        year_min = int(part["Årsmodell"].min())
        year_max = int(part["Årsmodell"].max())
        mileage_min = max(0, float(part["Miltal"].min()))
        mileage_max = float(part["Miltal"].max())

        year_grid = np.linspace(year_min, year_max, max(15, year_max - year_min + 1))
        mileage_grid = np.linspace(mileage_min, mileage_max, 25)
        years, mileages = np.meshgrid(year_grid, mileage_grid)
        grid = pd.DataFrame({"Årsmodell": years.ravel(), "Miltal": mileages.ravel()})
        prices = np.clip(model.predict(grid).reshape(years.shape), 0, None)

        fig.add_trace(
            go.Surface(
                x=years,
                y=mileages,
                z=prices,
                name=f"{variant} uppskattat marknadsvärde",
                showscale=False,
                opacity=0.22,
                colorscale=[[0, marker_colors[variant]], [1, marker_colors[variant]]],
            )
        )

        for year in range(year_min, year_max + 1):
            mile_line = np.linspace(mileage_min, mileage_max, 35)
            line_grid = pd.DataFrame(
                {
                    "Årsmodell": np.full_like(mile_line, year, dtype=float),
                    "Miltal": mile_line,
                }
            )
            price_line = np.clip(model.predict(line_grid), 0, None)
            fig.add_trace(
                go.Scatter3d(
                    x=np.full_like(mile_line, year, dtype=float),
                    y=mile_line,
                    z=price_line,
                    mode="lines",
                    showlegend=False,
                    line=dict(color=marker_colors[variant], width=3),
                )
            )

    def style_3d(fig: go.Figure, title: str) -> None:
        fig.update_layout(
            title=title,
            scene=dict(
                xaxis_title="Årsmodell",
                yaxis_title="Miltal (mil)",
                zaxis_title="Pris (kr)",
                camera=dict(eye=dict(x=1.55, y=1.45, z=1.15)),
            ),
            height=820,
            margin=dict(l=0, r=0, t=70, b=0),
        )

    fig_all = go.Figure()
    add_variant(fig_all, "GS")
    add_variant(fig_all, "GSA")
    style_3d(fig_all, "BMW R1250GS/GSA – gemensam 3D marknadsanalys")

    fig_gs = go.Figure()
    add_variant(fig_gs, "GS")
    style_3d(fig_gs, "BMW R1250GS – 3D marknadsanalys")

    fig_gsa = go.Figure()
    add_variant(fig_gsa, "GSA")
    style_3d(fig_gsa, "BMW R1250GSA – 3D marknadsanalys")

    ranking = usable.copy()
    ranking["Prisavvikelse (%)"] = pd.to_numeric(ranking["Prisavvikelse (%)"], errors="coerce")
    ranking = ranking.dropna(subset=["Prisavvikelse (%)"]).sort_values("Prisavvikelse (%)").head(15)
    if ranking.empty:
        fig_rank = go.Figure()
    else:
        fig_rank = px.bar(
            ranking.sort_values("Prisavvikelse (%)", ascending=False),
            x="Prisavvikelse (%)",
            y="Annonsrubrik",
            orientation="h",
            color="Modelltyp",
            hover_data=["Årsmodell", "Miltal", "Pris (kr)", "Säljare", "Direktlänk"],
            title="Mest prisvärda annonser enligt respektive GS/GSA-modell",
        )

    table_df = df.copy()

    def format_link(value: Any) -> str:
        if pd.isna(value) or not str(value).strip():
            return ""
        url = str(value)
        return f'<a href="{url}" target="_blank" rel="noopener">Öppna annons</a>'

    table_html = table_df.to_html(
        index=False,
        escape=False,
        border=0,
        classes="result-table",
        formatters={"Direktlänk": format_link} if "Direktlänk" in table_df.columns else None,
        na_rep="",
    )

    styles = """
    <style>
      body { font-family: Arial, sans-serif; margin: 0; }
      .intro, .results-wrap { padding: 0 18px; margin: 28px auto; }
      .results-scroll { overflow-x: auto; border: 1px solid #ddd; border-radius: 8px; }
      table.result-table { border-collapse: collapse; width: 100%; min-width: 1500px; font-size: 13px; }
      table.result-table th, table.result-table td {
        border-bottom: 1px solid #e5e5e5; padding: 8px 10px; text-align: left;
        vertical-align: top; white-space: nowrap;
      }
      table.result-table th { position: sticky; top: 0; background: #f5f5f5; z-index: 1; }
      table.result-table tbody tr:nth-child(even) { background: #fafafa; }
      table.result-table tbody tr:hover { background: #eef5ff; }
      table.result-table a { color: #0b57d0; text-decoration: none; }
      table.result-table a:hover { text-decoration: underline; }
    </style>
    """

    gs_count = int((usable["Modelltyp"] == "GS").sum())
    gsa_count = int((usable["Modelltyp"] == "GSA").sum())
    intro = f"""
    <div class="intro">
      <h2>BMW R1250GS/GSA – Blocketanalys</h2>
      <p><b>{len(usable)}</b> kompletta annonser analyserades: <b>{gs_count} GS</b> och <b>{gsa_count} GSA</b>.</p>
    </div>
    """

    with open(html_path, "w", encoding="utf-8") as file:
        file.write("<html><head><meta charset='utf-8'><title>R1250GS/GSA prisanalys</title>")
        file.write(styles)
        file.write("</head><body>")
        file.write(intro)
        file.write(fig_all.to_html(full_html=False, include_plotlyjs="cdn"))
        file.write("<hr style='margin:40px 0'>")
        file.write(fig_gs.to_html(full_html=False, include_plotlyjs=False))
        file.write("<hr style='margin:40px 0'>")
        file.write(fig_gsa.to_html(full_html=False, include_plotlyjs=False))
        file.write("<hr style='margin:40px 0'>")
        file.write(fig_rank.to_html(full_html=False, include_plotlyjs=False))
        file.write("<hr style='margin:40px 0'>")
        file.write(
            f'<div class="results-wrap"><h2>Resultattabell</h2>'
            f'<p>{len(table_df)} annonser i samma ordning som CSV-filen.</p>'
            f'<div class="results-scroll">{table_html}</div></div>'
        )
        file.write("</body></html>")

    print(f"Interaktiv analys och resultattabell sparad till {html_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="r1250gs_blocket.csv", help="CSV-fil")
    parser.add_argument("--html", default="r1250gs_prisanalys.html", help="HTML-rapport")
    parser.add_argument("--pages", type=int, default=10)
    parser.add_argument("--no-details", action="store_true")
    parser.add_argument("--debug-json", action="store_true")
    args = parser.parse_args()

    api = BlocketAPI()
    summaries_by_id: dict[int, dict[str, Any]] = {}
    anonymous: list[dict[str, Any]] = []
    empty_pages = 0

    print(f'Söker efter: "{QUERY}"')
    for page in range(1, args.pages + 1):
        try:
            response = api.search_mc(QUERY, page=page)
        except Exception as exc:
            print(f"Fel vid hämtning av sida {page}: {exc}", file=sys.stderr)
            break

        if args.debug_json:
            with open(f"debug_page_{page}.json", "w", encoding="utf-8") as file:
                json.dump(response, file, ensure_ascii=False, indent=2)

        candidates = collect_dict_candidates(response)
        new_count = 0
        for item in candidates:
            ad_id = find_id(item)
            if ad_id is None:
                anonymous.append(item)
                new_count += 1
            else:
                old = summaries_by_id.get(ad_id)
                if old is None:
                    summaries_by_id[ad_id] = item
                    new_count += 1
                elif len(flatten(item)) > len(flatten(old)):
                    summaries_by_id[ad_id] = item

        print(f"Sida {page}: {len(candidates)} kandidater, {new_count} nya.")
        empty_pages = empty_pages + 1 if not candidates else 0
        if empty_pages >= 2:
            break

    summaries = list(summaries_by_id.values()) + anonymous
    print(f"Totalt {len(summaries)} unika kandidater.")

    rows: list[dict[str, Any]] = []
    for i, summary in enumerate(summaries, start=1):
        details = None
        ad_id = find_id(summary)
        if ad_id and not args.no_details:
            try:
                details = api.get_ad(McAd(ad_id))
                time.sleep(0.15)
            except Exception as exc:
                print(f"Varning: detaljdata för {ad_id} misslyckades: {exc}", file=sys.stderr)

        row = make_row(summary, details)
        row["Annonsdatum"] = normalize_ad_date(fetch_updated_date(row["Direktlänk"]))
        combined = f"{row['Annonsrubrik']} {normalize_text(summary)} {normalize_text(details)}"
        if INCLUDE_RE.search(combined.replace("-", " ")) and not EXCLUDE_RE.search(combined):
            rows.append(row)

        print(
            f"[{i}/{len(summaries)}] {row['Årsmodell']} | {row['Miltal']} | "
            f"{row['Pris (kr)']} | {row['Annonsrubrik'][:60]}"
        )

    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["Annons-ID"]) or row["Direktlänk"] or row["Annonsrubrik"]
        unique[key] = row

    df = pd.DataFrame(list(unique.values()))
    if df.empty:
        print("Inga annonser hittades.")
        return 1

    df, models = add_market_analysis(df)
    price = pd.to_numeric(df["Pris (kr)"], errors="coerce")
    mileage = pd.to_numeric(df["Miltal"], errors="coerce")
    df["Pris per mil (kr/mil)"] = price / mileage.replace(0, pd.NA)

    order = [
        "Annons-ID",
        "Annonsdatum",
        "Annonsrubrik",
        "Modelltyp",
        "Årsmodell",
        "Miltal",
        "Pris (kr)",
        "Förväntat marknadspris (kr)",
        "Prisavvikelse (kr)",
        "Prisavvikelse (%)",
        "Pris per mil (kr/mil)",
        "Säljare",
        "Säljartyp",
        "Ort",
        "Motorvolym (cc)",
        "Effekt (hk)",
        "Regnr",
        "Chassinummer",
        "Direktlänk",
        "Beskrivning",
    ]
    df = df[[c for c in order if c in df.columns] + [c for c in df.columns if c not in order]]

    for col in ["Förväntat marknadspris (kr)", "Prisavvikelse (kr)", "Pris per mil (kr/mil)"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").round(0)
    df["Prisavvikelse (%)"] = pd.to_numeric(df["Prisavvikelse (%)"], errors="coerce").round(1)
    df = df.sort_values(
        ["Prisavvikelse (%)", "Årsmodell"],
        ascending=[True, False],
        na_position="last",
    )

    df.to_csv(args.out, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)
    print(f"Klart: {len(df)} annonser sparade till {args.out}")
    create_plotly_dashboard(df, args.html, models)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
