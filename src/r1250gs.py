#!/usr/bin/env python3
"""
Hämtar BMW R1250GS / R1250GS Adventure från Blocket via blocket-api,
klassificerar GS/GSA separat, exporterar CSV och skapar en interaktiv
prisanalys med separata värderingsmodeller för GS och GSA.

Installera:
    pip install blocket-api pandas plotly scikit-learn numpy requests beautifulsoup4

Kör:
    python src/r1250gs.py

Utdata:
    r1250gs_blocket.csv
    r1250gs_prisanalys.html
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from typing import Any

import requests
from bs4 import BeautifulSoup
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import PolynomialFeatures
from sklearn.pipeline import make_pipeline
from blocket_api import BlocketAPI, McAd

QUERY = "BMW R 1250 GS"
INCLUDE_RE = re.compile(r"\b(?:BMW\s+)?(?:MOTORRAD\s+)?R?\s*1250\s*GS(?:A|[\s-]*ADVENTURE)?\b", re.IGNORECASE)
EXCLUDE_RE = re.compile(r"\bR?\s*1300\s*GS\b|\bR?\s*1200\s*GS\b|\b1250\s*(?:R|RS|RT)\b", re.IGNORECASE)


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
            p = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, (dict, list)):
                out.update(flatten(value, p))
            else:
                out[p] = value
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            p = f"{prefix}[{i}]"
            if isinstance(value, (dict, list)):
                out.update(flatten(value, p))
            else:
                out[p] = value
    return out


def find_value(obj: Any, candidate_names: list[str]) -> Any:
    flat = flatten(obj)
    wanted = {name.lower() for name in candidate_names}
    for key, value in flat.items():
        last = re.sub(r"\[\d+\]$", "", key.split(".")[-1]).lower()
        if last in wanted and value not in (None, "", []):
            return value
    for key, value in flat.items():
        k = key.lower()
        if any(name in k for name in wanted) and value not in (None, "", []):
            return value
    return None


def find_int(obj: Any, candidate_names: list[str]) -> int | None:
    value = find_value(obj, candidate_names)
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"-?\d[\d\s\xa0.,]*", normalize_text(value))
    if not m:
        return None
    raw = re.sub(r"[^\d-]", "", m.group(0))
    try:
        return int(raw)
    except ValueError:
        return None


def find_title(obj: Any) -> str:
    return normalize_text(find_value(obj, ["heading", "title", "subject", "name", "ad_title", "display_name"]))


def find_id(obj: Any) -> int | None:
    value = find_value(obj, ["id", "ad_id", "adid", "listing_id", "item_id", "object_id"])
    if value is None:
        return None
    if isinstance(value, int):
        return value
    m = re.search(r"\d{5,}", normalize_text(value))
    return int(m.group(0)) if m else None


def find_url(obj: Any, ad_id: int | None) -> str:
    value = find_value(obj, ["url", "web_url", "canonical_url", "share_url", "href", "link"])
    text = normalize_text(value)
    if text.startswith("http"):
        return text
    if text.startswith("/"):
        return "https://www.blocket.se" + text
    if ad_id:
        return f"https://www.blocket.se/mobility/item/{ad_id}"
    return ""


def looks_like_r1250gs(obj: Any) -> bool:
    text = normalize_text(obj).replace("-", " ")
    return bool(INCLUDE_RE.search(text)) and not bool(EXCLUDE_RE.search(text))


def collect_dict_candidates(obj: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if find_id(x) is not None and find_title(x) and looks_like_r1250gs(x):
                found.append(x)
            for value in x.values():
                walk(value)
        elif isinstance(x, list):
            for value in x:
                walk(value)
    walk(obj)
    deduped: dict[int, dict[str, Any]] = {}
    no_id: list[dict[str, Any]] = []
    for item in found:
        ad_id = find_id(item)
        if ad_id is None:
            no_id.append(item)
        else:
            old = deduped.get(ad_id)
            if old is None or len(flatten(item)) > len(flatten(old)):
                deduped[ad_id] = item
    return list(deduped.values()) + no_id


def seller_type_from(obj: Any) -> str:
    value = normalize_text(find_value(obj, ["seller_type", "dealer_type", "organization_type", "account_type"])).lower()
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


SWEDISH_MONTHS = "januari|februari|mars|april|maj|juni|juli|augusti|september|oktober|november|december"
MONTH_MAP = {"januari":1,"februari":2,"mars":3,"april":4,"maj":5,"juni":6,"juli":7,"augusti":8,"september":9,"oktober":10,"november":11,"december":12}


def normalize_ad_date(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-zÅÄÖåäö]+)\s+(\d{4})(?:,\s*(\d{1,2}):(\d{2}))?", value)
    if not m:
        return value
    day, month_name, year = int(m.group(1)), m.group(2).lower(), int(m.group(3))
    hour, minute = int(m.group(4) or 0), int(m.group(5) or 0)
    month = MONTH_MAP.get(month_name)
    if month is None:
        return value
    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"


def fetch_updated_date(url: str, timeout: float = 12.0) -> str:
    if not url:
        return ""
    headers = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36","Accept-Language":"sv-SE,sv;q=0.9,en;q=0.8"}
    try:
        response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Varning: kunde inte läsa annonsdatum från {url}: {exc}", file=sys.stderr)
        return ""
    page_text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
    pattern = re.compile(rf"\bUppdaterad\s+(\d{{1,2}}\s+(?:{SWEDISH_MONTHS})\s+\d{{4}},\s+\d{{1,2}}:\d{{2}})", re.IGNORECASE)
    match = pattern.search(page_text)
    if match:
        return match.group(1)
    pattern_no_time = re.compile(rf"\bUppdaterad\s+(\d{{1,2}}\s+(?:{SWEDISH_MONTHS})\s+\d{{4}})", re.IGNORECASE)
    match = pattern_no_time.search(page_text)
    return match.group(1) if match else ""


def make_row(summary: dict[str, Any], details: dict[str, Any] | None) -> dict[str, Any]:
    merged = {"summary": summary, "details": details or {}}
    source = details if details else summary
    ad_id = find_id(summary) or find_id(source)
    return {
        "Annons-ID": ad_id or "", "Annonsdatum": "", "Annonsrubrik": find_title(source) or find_title(summary),
        "Modelltyp": classify_variant(merged),
        "Årsmodell": find_int(merged, ["year","model_year","registration_year","year_model","modelyear"]) or "",
        "Miltal": find_int(merged, ["mileage","milage","mileage_mil","odometer"]) or "",
        "Pris (kr)": find_int(merged, ["price","amount","price_value","sales_price"]) or "",
        "Säljare": normalize_text(find_value(merged,["seller_name","dealer_name","organization_name","company_name","store_name","seller"])),
        "Säljartyp": seller_type_from(merged),
        "Ort": normalize_text(find_value(merged,["location","city","municipality","area","region","address"])),
        "Motorvolym (cc)": find_int(merged,["engine_volume","engine_volume_cc","cylinder_volume","displacement"]) or "",
        "Effekt (hk)": find_int(merged,["horsepower","hp","effect","power_hp"]) or "",
        "Regnr": normalize_text(find_value(merged,["registration_number","registration","reg_number","regno","license_plate"])),
        "Chassinummer": normalize_text(find_value(merged,["Chassinummer","chassinummer","vin","VIN","chassis_number","vehicle_identification_number"])),
        "Direktlänk": find_url(source, ad_id),
        "Beskrivning": normalize_text(find_value(merged,["description","body","text","ad_text"])),
    }


def add_market_analysis(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    for col in ["Årsmodell","Miltal","Pris (kr)"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["Förväntat marknadspris (kr)"] = pd.NA
    df["Prisavvikelse (kr)"] = pd.NA
    df["Prisavvikelse (%)"] = pd.NA
    models: dict[str, object] = {}
    for variant in ["GS","GSA"]:
        mask = (df["Modelltyp"] == variant) & df["Årsmodell"].notna() & df["Miltal"].notna() & df["Pris (kr)"].notna()
        subset = df.loc[mask].copy()
        print(f"{variant}: {len(subset)} kompletta annonser för prisanalys.")
        if len(subset) < 8:
            print(f"  För få {variant}-annonser för separat regression ({len(subset)} st).")
            continue
        X, y = subset[["Årsmodell","Miltal"]], subset["Pris (kr)"]
        model = make_pipeline(PolynomialFeatures(degree=2, include_bias=False), LinearRegression())
        model.fit(X, y)
        models[variant] = model
        df.loc[mask,"Förväntat marknadspris (kr)"] = model.predict(X).clip(min=0)
    expected = pd.to_numeric(df["Förväntat marknadspris (kr)"], errors="coerce")
    price = pd.to_numeric(df["Pris (kr)"], errors="coerce")
    df["Prisavvikelse (kr)"] = price - expected
    df["Prisavvikelse (%)"] = 100 * df["Prisavvikelse (kr)"] / expected
    return df, models


def create_plotly_dashboard(df: pd.DataFrame, html_path: str, models: dict[str, object]) -> None:
    usable = df.dropna(subset=["Årsmodell","Miltal","Pris (kr)","Modelltyp"]).copy()
    if usable.empty:
        return
    import numpy as np
    marker_symbols = {"GS":"circle","GSA":"diamond"}
    marker_colors = {"GS":"#1f77b4","GSA":"#d62728"}
    def add_variant(fig: go.Figure, variant: str) -> None:
        part = usable[usable["Modelltyp"] == variant].copy()
        if part.empty:
            return
        cols = ["Annonsrubrik","Säljare","Ort","Direktlänk","Förväntat marknadspris (kr)","Prisavvikelse (%)","Annonsdatum"]
        for c in cols:
            if c not in part.columns: part[c] = ""
        fig.add_trace(go.Scatter3d(x=part["Årsmodell"],y=part["Miltal"],z=part["Pris (kr)"],mode="markers",name=f"{variant} annonser",marker=dict(size=6,symbol=marker_symbols[variant],color=marker_colors[variant],opacity=.9),customdata=part[cols].to_numpy(),hovertemplate="<b>%{customdata[0]}</b><br>Årsmodell: %{x:.0f}<br>Miltal: %{y:.0f} mil<br>Pris: %{z:,.0f} kr<br>Förväntat: %{customdata[4]:,.0f} kr<br>Avvikelse: %{customdata[5]:+.1f}%<br>Annonsdatum: %{customdata[6]}<br>%{customdata[3]}<extra></extra>"))
        if variant not in models: return
        model = models[variant]
        ymin,ymax=int(part["Årsmodell"].min()),int(part["Årsmodell"].max())
        mmin,mmax=max(0,float(part["Miltal"].min())),float(part["Miltal"].max())
        yg=np.linspace(ymin,ymax,max(15,ymax-ymin+1)); mg=np.linspace(mmin,mmax,25); YR,MI=np.meshgrid(yg,mg)
        PR=np.clip(model.predict(pd.DataFrame({"Årsmodell":YR.ravel(),"Miltal":MI.ravel()})).reshape(YR.shape),0,None)
        fig.add_trace(go.Surface(x=YR,y=MI,z=PR,name=f"{variant} uppskattat marknadsvärde",showscale=False,opacity=.22,colorscale=[[0,marker_colors[variant]],[1,marker_colors[variant]]]))
        for year in range(ymin,ymax+1):
            ml=np.linspace(mmin,mmax,35); prices=np.clip(model.predict(pd.DataFrame({"Årsmodell":np.full_like(ml,year,dtype=float),"Miltal":ml})),0,None)
            fig.add_trace(go.Scatter3d(x=np.full_like(ml,year,dtype=float),y=ml,z=prices,mode="lines",showlegend=False,line=dict(color=marker_colors[variant],width=3)))
    def style(fig,title):
        fig.update_layout(title=title,scene=dict(xaxis_title="Årsmodell",yaxis_title="Miltal (mil)",zaxis_title="Pris (kr)"),height=820)
    fig_all=go.Figure(); add_variant(fig_all,"GS"); add_variant(fig_all,"GSA"); style(fig_all,"BMW R1250GS/GSA – gemensam 3D marknadsanalys")
    fig_gs=go.Figure(); add_variant(fig_gs,"GS"); style(fig_gs,"BMW R1250GS – 3D marknadsanalys")
    fig_gsa=go.Figure(); add_variant(fig_gsa,"GSA"); style(fig_gsa,"BMW R1250GSA – 3D marknadsanalys")
    ranking=usable.copy(); ranking["Prisavvikelse (%)"]=pd.to_numeric(ranking["Prisavvikelse (%)"],errors="coerce"); ranking=ranking.dropna(subset=["Prisavvikelse (%)"]).sort_values("Prisavvikelse (%)").head(15)
    fig_rank=px.bar(ranking.sort_values("Prisavvikelse (%)",ascending=False),x="Prisavvikelse (%)",y="Annonsrubrik",orientation="h",color="Modelltyp",title="Mest prisvärda annonser") if not ranking.empty else go.Figure()
    with open(html_path,"w",encoding="utf-8") as f:
        f.write("<html><head><meta charset='utf-8'><title>R1250GS/GSA 3D prisanalys</title></head><body>")
        f.write(fig_all.to_html(full_html=False,include_plotlyjs="cdn")); f.write(fig_gs.to_html(full_html=False,include_plotlyjs=False)); f.write(fig_gsa.to_html(full_html=False,include_plotlyjs=False)); f.write(fig_rank.to_html(full_html=False,include_plotlyjs=False)); f.write("</body></html>")


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--out",default="r1250gs_blocket.csv"); parser.add_argument("--html",default="r1250gs_prisanalys.html"); parser.add_argument("--pages",type=int,default=10); parser.add_argument("--no-details",action="store_true"); parser.add_argument("--debug-json",action="store_true"); args=parser.parse_args()
    api=BlocketAPI(); summaries_by_id={}; anonymous=[]; empty_pages=0
    print(f'Söker efter: "{QUERY}"')
    for page in range(1,args.pages+1):
        try: response=api.search_mc(QUERY,page=page)
        except Exception as exc: print(f"Fel vid hämtning av sida {page}: {exc}",file=sys.stderr); break
        if args.debug_json:
            with open(f"debug_page_{page}.json","w",encoding="utf-8") as f: json.dump(response,f,ensure_ascii=False,indent=2)
        candidates=collect_dict_candidates(response)
        for item in candidates:
            ad_id=find_id(item)
            if ad_id is None: anonymous.append(item)
            elif ad_id not in summaries_by_id or len(flatten(item))>len(flatten(summaries_by_id[ad_id])): summaries_by_id[ad_id]=item
        empty_pages=empty_pages+1 if not candidates else 0
        if empty_pages>=2: break
    summaries=list(summaries_by_id.values())+anonymous; rows=[]
    for summary in summaries:
        details=None; ad_id=find_id(summary)
        if ad_id and not args.no_details:
            try: details=api.get_ad(McAd(ad_id)); time.sleep(.15)
            except Exception as exc: print(f"Varning: detaljdata för {ad_id} misslyckades: {exc}",file=sys.stderr)
        row=make_row(summary,details); row["Annonsdatum"]=normalize_ad_date(fetch_updated_date(row["Direktlänk"]))
        combined=f"{row['Annonsrubrik']} {normalize_text(summary)} {normalize_text(details)}"
        if INCLUDE_RE.search(combined.replace("-"," ")) and not EXCLUDE_RE.search(combined): rows.append(row)
    unique={}
    for row in rows: unique[str(row["Annons-ID"]) or row["Direktlänk"] or row["Annonsrubrik"]]=row
    df=pd.DataFrame(list(unique.values()))
    if df.empty: print("Inga annonser hittades."); return 1
    df,models=add_market_analysis(df)
    price=pd.to_numeric(df["Pris (kr)"],errors="coerce"); mileage=pd.to_numeric(df["Miltal"],errors="coerce"); df["Pris per mil (kr/mil)"]=price/mileage.replace(0,pd.NA)
    order=["Annons-ID","Annonsdatum","Annonsrubrik","Modelltyp","Årsmodell","Miltal","Pris (kr)","Förväntat marknadspris (kr)","Prisavvikelse (kr)","Prisavvikelse (%)","Pris per mil (kr/mil)","Säljare","Säljartyp","Ort","Motorvolym (cc)","Effekt (hk)","Regnr","Chassinummer","Direktlänk","Beskrivning"]
    df=df[[c for c in order if c in df.columns]+[c for c in df.columns if c not in order]]
    for col in ["Förväntat marknadspris (kr)","Prisavvikelse (kr)","Pris per mil (kr/mil)"]: df[col]=pd.to_numeric(df[col],errors="coerce").round(0)
    df["Prisavvikelse (%)"]=pd.to_numeric(df["Prisavvikelse (%)"],errors="coerce").round(1); df=df.sort_values(["Prisavvikelse (%)","Årsmodell"],ascending=[True,False],na_position="last")
    df.to_csv(args.out,index=False,encoding="utf-8-sig",quoting=csv.QUOTE_MINIMAL); print(f"Klart: {len(df)} annonser sparade till {args.out}"); create_plotly_dashboard(df,args.html,models); return 0


if __name__ == "__main__":
    raise SystemExit(main())
