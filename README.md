# Prono insight

Application Streamlit d’aide à l’analyse football avec dashboard, imports
API-Football, widgets API-Sports, statistiques, estimations 1/N/2 expérimentales
et suivi persistant des mises à jour.

Les scores 1/N/2 sont des estimations internes issues de règles statistiques.
Ils ne constituent ni une fiabilité calibrée ni un conseil de pari.

Lorsqu’un conseil API-Football informatif est disponible pour une rencontre
programmée, l’analyse lui attribue un poids adaptatif et minoritaire de 10 à
30 %, selon sa précision et la richesse des comparaisons fournies. Les réponses
neutres 33/33/33 sont ignorées. Les probabilités, l’accord ou le désaccord et
les comparaisons API (forme, attaque, défense, Poisson, face-à-face et buts)
restent visibles. La page « Mise à jour » synchronise tous les conseils futurs
sans retélécharger ceux déjà enregistrés.

## Quick Start

1. Create virtualenv and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and set your credentials:

```bash
cp .env.example .env
```

Use unique values for `APP_USERNAME` and `APP_PASSWORD`; known defaults such
as `admin/admin` are rejected. Authentication remains in the Streamlit session
and is never stored in the URL.

The live widget uses `API_FOOTBALL_WIDGET_KEY` when configured, otherwise it
falls back to `API_FOOTBALL_KEY`. Because the widget runs in the browser, the
selected key is visible to authenticated users. Use a separate key restricted
by domain and quota if that exposure becomes a concern.

3. Run the Streamlit app:

```bash
streamlit run app.py
```

The app creates SQLite tables on first run.

## Checks

```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py components database pages services scripts
python -m pip check
```
