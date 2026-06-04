import json

import pandas as pd
import streamlit as st

from components import ui
from database.database import engine
from services import import_service


LEAGUE_FALLBACK_NAMES = {
    2: "Ligue des Champions",
    39: "Premier League",
    61: "Ligue 1",
    78: "Bundesliga",
    135: "Serie A",
    140: "La Liga",
}


def _read_table(query: str) -> pd.DataFrame:
    try:
        return pd.read_sql(query, engine)
    except Exception:
        return pd.DataFrame()


def _format_datetime(value):
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return value
    return timestamp.strftime("%d/%m/%Y %H:%M:%S")


def _compact_json(value):
    if not value or pd.isna(value):
        return "Non applicable"
    try:
        parsed = json.loads(value)
        if parsed is None:
            return "Non applicable"
        if isinstance(parsed, list):
            return ", ".join(str(item) for item in parsed)
        if isinstance(parsed, dict):
            return json.dumps(parsed, ensure_ascii=False)
    except Exception:
        pass
    return str(value)


def _league_name_map() -> dict[int, str]:
    try:
        leagues = pd.read_sql("SELECT id, name, country FROM leagues", engine)
        return {
            int(row.id): f"{row.name} ({row.country})" if row.country else row.name
            for row in leagues.itertuples()
        }
    except Exception:
        return LEAGUE_FALLBACK_NAMES


def _league_label(league_id, names: dict[int, str]) -> str:
    try:
        value = int(league_id)
    except Exception:
        return str(league_id)
    return names.get(value) or LEAGUE_FALLBACK_NAMES.get(value) or f"Championnat {value}"


def _format_leagues(value, names: dict[int, str]) -> str:
    parsed = _parse_json_or_none(value)
    if parsed is None:
        return "Non applicable"
    if isinstance(parsed, list):
        labels = []
        for item in parsed:
            if isinstance(item, dict):
                league = _league_label(item.get("league_id"), names)
                season = item.get("season")
                labels.append(f"{league} - {season}" if season else league)
            else:
                labels.append(_league_label(item, names))
        return ", ".join(labels) if labels else "Non applicable"
    if isinstance(parsed, dict):
        if "league_id" in parsed:
            league = _league_label(parsed.get("league_id"), names)
            season = parsed.get("season")
            return f"{league} - {season}" if season else league
        return json.dumps(parsed, ensure_ascii=False)
    return str(parsed)


def _parse_json_or_none(value):
    if not value or pd.isna(value):
        return None
    try:
        return json.loads(value)
    except Exception:
        return str(value)


def _load_update_logs() -> pd.DataFrame:
    import_service.init_db()
    league_names = _league_name_map()
    logs = _read_table("SELECT * FROM update_log ORDER BY finished_at DESC, id DESC")
    if logs.empty:
        return logs
    logs["Début"] = logs["started_at"].apply(_format_datetime)
    logs["Fin"] = logs["finished_at"].apply(_format_datetime)
    logs["Durée (s)"] = logs["duration_seconds"].fillna(0)
    logs["Type"] = logs["event_type"]
    logs["Statut"] = logs["status"]
    logs["Raison"] = logs["reason"].fillna("")
    logs["Ligues / championnats"] = logs["leagues"].apply(lambda value: _format_leagues(value, league_names))
    logs["Saisons"] = logs["seasons"].apply(_compact_json)
    logs["Saisons forcées"] = logs["forced_seasons"].apply(_compact_json)
    logs["Erreur"] = logs["error"].fillna("")
    return logs


def _load_connection_logs() -> pd.DataFrame:
    import_service.init_db()
    logs = _read_table("SELECT * FROM connection_log ORDER BY connected_at DESC, id DESC")
    if logs.empty:
        return logs
    logs["Connexion"] = logs["connected_at"].apply(_format_datetime)
    logs["MAJ en cours effectuée"] = logs["refreshed_current"].map({1: "Oui", 0: "Non"}).fillna("Non")
    return logs


def _load_sync_state() -> pd.DataFrame:
    import_service.init_db()
    state = _read_table("SELECT key, value, updated_at FROM sync_state ORDER BY updated_at DESC")
    if state.empty:
        return state
    state["Dernière modification"] = state["updated_at"].apply(_format_datetime)
    return state.rename(columns={"key": "Clé", "value": "Valeur"})


def show():
    ui.page_hero(
        "Logs des mises à jour",
        "Historique persistant des connexions, synchronisations automatiques et imports manuels enregistrés dans SQLite.",
    )

    logs = _load_update_logs()
    connections = _load_connection_logs()
    sync_state = _load_sync_state()

    cols = st.columns(4)
    cols[0].metric("Événements MAJ", len(logs))
    cols[1].metric("Effectuées", int((logs["status"] == "effectuée").sum()) if not logs.empty else 0)
    cols[2].metric("Ignorées", int((logs["status"] == "ignorée").sum()) if not logs.empty else 0)
    cols[3].metric("Erreurs", int((logs["status"] == "erreur").sum()) if not logs.empty else 0)

    tab_updates, tab_connections, tab_state = st.tabs(["Mises à jour", "Connexions", "État système"])

    with tab_updates:
        if logs.empty:
            st.info("Aucun log de mise à jour enregistré pour le moment.")
        else:
            filter_cols = st.columns(3)
            status_options = sorted(logs["status"].dropna().unique().tolist())
            type_options = sorted(logs["event_type"].dropna().unique().tolist())
            selected_status = filter_cols[0].multiselect("Statuts", status_options, default=status_options)
            selected_types = filter_cols[1].multiselect("Types", type_options, default=type_options)
            limit = filter_cols[2].number_input("Nombre de lignes", min_value=10, max_value=500, value=100, step=10)

            filtered = logs[
                logs["status"].isin(selected_status)
                & logs["event_type"].isin(selected_types)
            ].head(int(limit))

            st.dataframe(
                filtered[
                    [
                        "id",
                        "Type",
                        "Statut",
                        "Début",
                        "Fin",
                        "Durée (s)",
                        "Raison",
                        "Ligues / championnats",
                        "Erreur",
                    ]
                ],
                hide_index=True,
                use_container_width=True,
            )

            selected_id = st.selectbox("Voir le détail brut", options=filtered["id"].tolist())
            selected_row = filtered[filtered["id"] == selected_id].iloc[0]
            st.json(
                {
                    "id": int(selected_row["id"]),
                    "type": selected_row["event_type"],
                    "status": selected_row["status"],
                    "started_at": selected_row["started_at"],
                    "finished_at": selected_row["finished_at"],
                    "duration_seconds": selected_row["duration_seconds"],
                    "reason": selected_row["reason"],
                    "leagues": _parse_json_or_none(selected_row["leagues"]),
                    "seasons": _parse_json_or_none(selected_row["seasons"]),
                    "forced_seasons": _parse_json_or_none(selected_row["forced_seasons"]),
                    "details": _parse_json_or_none(selected_row["details"]),
                    "error": selected_row["error"],
                }
            )

    with tab_connections:
        if connections.empty:
            st.info("Aucune connexion enregistrée.")
        else:
            st.dataframe(
                connections[["id", "Connexion", "MAJ en cours effectuée"]],
                hide_index=True,
                use_container_width=True,
            )

    with tab_state:
        if sync_state.empty:
            st.info("Aucun état système enregistré.")
        else:
            st.dataframe(
                sync_state[["Clé", "Valeur", "Dernière modification"]],
                hide_index=True,
                use_container_width=True,
            )


if __name__ == "__main__":
    ui.run_direct_page("Logs des mises à jour", show)
