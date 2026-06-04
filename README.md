# Football Prono AI

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

3. Run the Streamlit app:

```bash
streamlit run app.py
```

The app creates SQLite tables on first run.
