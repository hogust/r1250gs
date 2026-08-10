# R1250GS Blocket-analys

Pythonverktyg för att hämta BMW R1250GS och R1250GS Adventure-annonser från Blocket och analysera prisnivån.

## Funktioner

- Hämtar MC-annonser via `blocket-api`.
- Klassificerar vanlig **GS** och **GSA / Adventure** separat.
- Hämtar detaljdata per annons, inklusive chassinummer när det finns.
- Hämtar fältet **Uppdaterad** från Blockets annonssida och sparar det som `YYYY-MM-DD HH:MM`.
- Exporterar annonserna till CSV.
- Beräknar separat uppskattat marknadspris för GS och GSA utifrån årsmodell och miltal.
- Visar prisavvikelse i kronor och procent.
- Skapar interaktiva 3D-grafer för GS+GSA, GS och GSA samt en ranking över annonser som ligger under modellens uppskattade marknadsvärde.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

På Debian/Ubuntu kan paketet för venv behöva installeras först, exempelvis:

```bash
sudo apt install python3-venv
```

## Körning

```bash
python r1250gs.py
```

Standardutdata:

- `r1250gs_blocket.csv`
- `r1250gs_prisanalys.html`

### Alternativ

```bash
python r1250gs.py --out r1250gs.csv
python r1250gs.py --html analys.html
python r1250gs.py --pages 10
python r1250gs.py --no-details
python r1250gs.py --debug-json
```

## Prisanalys

GS och GSA modelleras separat. För respektive modelltyp används en andragradsregression med:

- årsmodell
- miltal

Resultatet är en uppskattning av aktuella **utropspriser**, inte faktiska försäljningspriser. Utrustning, skick, servicehistorik och tillbehör ingår ännu inte i modellen.

## Genererade filer

CSV-, HTML- och debugfiler ignoreras av Git via `.gitignore` och skapas lokalt när skriptet körs.
