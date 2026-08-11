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

## Automatisk körning och Cloudflare Pages

Branchen `feature/cloudflare-pages-hosting` innehåller workflowet `.github/workflows/update-analysis.yml`.

Workflowet:

- kör analysen automatiskt varje dag kl. 04:30 UTC när workflowet finns på default-branchen,
- kan startas manuellt via GitHub Actions,
- körs även vid push till Cloudflare-feature-branchen för test,
- bygger `public/index.html` och `public/r1250gs_blocket.csv`,
- sparar resultatet som en GitHub Actions-artifact,
- publicerar `public/` till Cloudflare Pages-projektet `r1250gs` när Cloudflare-secrets är konfigurerade.

### GitHub Secrets

Lägg följande repository secrets under **Settings → Secrets and variables → Actions**:

- `CLOUDFLARE_ACCOUNT_ID` – Cloudflare-kontots Account ID.
- `CLOUDFLARE_API_TOKEN` – ett Cloudflare API-token med **Account → Cloudflare Pages → Edit** för aktuellt konto.

Cloudflare Pages-projektet ska heta `r1250gs`. För Direct Upload kan projektet skapas i Cloudflare Dashboard eller med Wrangler:

```bash
npx wrangler pages project create r1250gs --production-branch main
```

När dessa två secrets finns deployar GitHub Actions automatiskt efter en lyckad Pythonkörning. Pushar från feature-branchen blir preview-deployments; när workflowet senare ligger på `main` blir den schemalagda körningen produktionsflödet.
