# Prono insight

Application Streamlit de pronostic IA football avec dashboard, imports API-Football, widgets API-Sports, analyses, predictions et logs de mises a jour persistants.

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
