import pandas as pd
import streamlit as st


POSITION_LABELS = {
    "G": "Gardien",
    "D": "Défenseur",
    "M": "Milieu",
    "F": "Attaquant",
    "Goalkeeper": "Gardien",
    "Defender": "Défenseur",
    "Midfielder": "Milieu",
    "Attacker": "Attaquant",
}


def _players_table(players: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "N°": str(player.get("number") or "-"),
                "Joueur": str(player.get("player_name") or "Joueur"),
                "Poste": POSITION_LABELS.get(
                    str(player.get("position") or player.get("games_position") or ""),
                    str(player.get("position") or player.get("games_position") or "-"),
                ),
                "Forme": (
                    f"{float(player['form_rating']):.2f}"
                    if player.get("form_rating") is not None
                    else "-"
                ),
                "Matchs récents": str(int(player.get("recent_matches") or 0)),
                "Buts + passes": str(
                    int(player.get("recent_goals") or 0)
                    + int(player.get("recent_assists") or 0)
                ),
            }
            for player in players
        ]
    )


def _render_team(team: dict | None, name: str):
    with st.container(border=True):
        st.markdown(f"### {name}")
        if not team:
            st.info("Composition probable indisponible pour cette équipe.")
            return
        label = (
            "officielle"
            if team.get("official")
            else "probable — estimation statistique non officielle"
        )
        st.caption(
            f"Composition {label} · {team.get('formation') or 'dispositif inconnu'} · "
            f"{team.get('formation_source') or 'source non précisée'}"
        )
        if not team.get("official"):
            st.caption(
                f"Confiance estimée : "
                f"{round(float(team.get('projection_confidence') or 0) * 100)} % · "
                f"{team.get('player_source') or 'effectif historique disponible'}"
            )
        metrics = st.columns(2)
        metrics[0].metric("Forme du onze", f"{team.get('form_score', 50)} / 100")
        metrics[1].metric("Note moyenne", team.get("average_rating", "-"))
        line_ratings = team.get("line_ratings") or {}
        st.caption(
            "Notes par ligne — "
            f"défense {line_ratings.get('defense', '-')} · "
            f"milieu {line_ratings.get('midfield', '-')} · "
            f"attaque {line_ratings.get('attack', '-')}"
        )
        players = _players_table(team.get("starters") or [])
        if not players.empty:
            st.dataframe(players, hide_index=True, width="stretch")

        profile = team.get("tactical_profile") or {}
        st.markdown("**Plan de jeu associé au dispositif**")
        st.write(profile.get("attack") or team.get("strategy") or "Indisponible")
        with st.expander("Étude tactique par phase"):
            st.markdown("**Relance**")
            st.write(profile.get("build_up") or "-")
            st.markdown("**Attaque placée**")
            st.write(profile.get("attack") or "-")
            st.markdown("**Organisation défensive**")
            st.write(profile.get("defense") or "-")
            st.markdown("**Transitions**")
            st.write(profile.get("transition") or "-")
            if profile.get("strengths"):
                st.markdown("**Forces structurelles**")
                st.write(" · ".join(profile["strengths"]))
            if profile.get("risks"):
                st.markdown("**Risques structurels**")
                st.write(" · ".join(profile["risks"]))
            history = team.get("formation_history") or []
            if history:
                st.markdown("**Dispositifs historiques pondérés**")
                for item in history:
                    st.write(
                        f"- {item.get('formation')} : {int(item.get('uses') or 0)} "
                        "match(s) observé(s)"
                    )


def render_match_intelligence(
    intelligence: dict | None,
    home_name: str,
    away_name: str,
    show_players: bool = True,
):
    if not intelligence or not intelligence.get("complete"):
        st.info(
            "Données de composition insuffisantes. Synchronisez les joueurs des "
            "deux équipes pour activer l’analyse tactique."
        )
        return

    if show_players:
        columns = st.columns(2)
        with columns[0]:
            _render_team(intelligence.get("home"), home_name)
        with columns[1]:
            _render_team(intelligence.get("away"), away_name)

    tactical = intelligence.get("tactical")
    if not tactical:
        return
    st.markdown("### Opposition tactique")
    summary = st.columns(4)
    summary[0].metric(home_name, tactical["home_formation"])
    summary[1].metric(away_name, tactical["away_formation"])
    edge = float(tactical.get("edge") or 0)
    edge_label = home_name if edge > 0.5 else away_name if edge < -0.5 else "Équilibre"
    summary[2].metric("Avantage tactique", edge_label)
    summary[3].metric(
        "Fiabilité", f"{round(float(tactical.get('reliability') or 0) * 100)} %"
    )
    st.write(tactical.get("structural_reading") or "")

    advantages = st.columns(2)
    with advantages[0]:
        st.markdown(f"**Leviers pour {home_name}**")
        for item in tactical.get("home_advantages") or ["Aucun avantage net détecté."]:
            st.write(f"- {item}")
    with advantages[1]:
        st.markdown(f"**Leviers pour {away_name}**")
        for item in tactical.get("away_advantages") or ["Aucun avantage net détecté."]:
            st.write(f"- {item}")

    matchups = tactical.get("line_matchups") or {}
    st.caption(
        "Écarts de notes — attaque domicile / défense extérieure "
        f"{matchups.get('home_attack_vs_away_defense', 0):+.2f} · "
        "milieux "
        f"{matchups.get('home_midfield_vs_away_midfield', 0):+.2f} · "
        "attaque extérieure / défense domicile "
        f"{matchups.get('away_attack_vs_home_defense', 0):+.2f}."
    )
    st.caption(
        "L’analyse décrit des tendances probables du dispositif. Les consignes, "
        "rôles individuels et changements en cours de match restent inconnus."
    )
