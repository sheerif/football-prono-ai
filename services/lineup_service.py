import datetime
import json
import time
from collections import defaultdict

import pandas as pd
from sqlalchemy import text

from database import models
from database.database import SessionLocal, engine
from services.api_football import ApiFootballClient


client = ApiFootballClient()
_schema_ready = False

FORMATION_PLANS = {
    "4-3-3": "Largeur offensive, pressing haut possible et trois joueurs pour attaquer la dernière ligne.",
    "4-2-3-1": "Double pivot pour sécuriser l’axe, transitions rapides et soutien d’un meneur derrière l’attaquant.",
    "4-4-2": "Deux lignes compactes, présence de deux attaquants et recherche possible des couloirs.",
    "3-5-2": "Densité dans l’axe, pistons très sollicités et deux attaquants pour les transitions.",
    "3-4-3": "Relance à trois, largeur donnée par les pistons et pression avec une ligne offensive de trois.",
    "5-3-2": "Bloc défensif renforcé, protection de la surface et sorties rapides vers deux attaquants.",
}

FORMATION_PROFILES = {
    "4-3-3": {
        "build_up": "Sortie à quatre avec triangles latéraux et sentinelle disponible entre les lignes.",
        "attack": "Largeur forte, ailiers dans les demi-espaces et occupation rapide des cinq couloirs.",
        "defense": "Pressing à trois devant, bloc médian compact mais espace possible derrière les latéraux.",
        "transition": "Projection rapide des ailiers ; contre-pressing immédiat après la perte.",
        "strengths": ["largeur offensive", "pressing haut", "surnombre au milieu"],
        "risks": ["dos des latéraux", "isolement du numéro 9", "espace autour de la sentinelle"],
        "metrics": {"width": 8, "central": 7, "security": 6, "pressing": 8, "transition": 8},
    },
    "4-2-3-1": {
        "build_up": "Double pivot pour sécuriser la première relance et libérer le meneur entre les lignes.",
        "attack": "Création axiale autour du numéro 10 avec largeur assurée par les ailiers ou les latéraux.",
        "defense": "Bloc en 4-4-1-1, bonne protection de l’axe et couverture des transitions adverses.",
        "transition": "Sorties rapides vers le meneur et les ailes derrière le milieu adverse.",
        "strengths": ["protection axiale", "présence entre les lignes", "équilibre des transitions"],
        "risks": ["avant-centre isolé", "dépendance au numéro 10", "espaces si les deux pivots reculent"],
        "metrics": {"width": 7, "central": 8, "security": 8, "pressing": 6, "transition": 8},
    },
    "4-4-2": {
        "build_up": "Relance simple vers les couloirs ou les deux attaquants, avec recherche fréquente du second ballon.",
        "attack": "Deux joueurs dans la surface, centres et combinaisons latérales soutenues par les milieux.",
        "defense": "Deux lignes de quatre compactes, fermeture des côtés et défense de surface solide.",
        "transition": "Jeu direct vers le duo offensif dès la récupération.",
        "strengths": ["compacité", "présence dans la surface", "couverture des couloirs"],
        "risks": ["infériorité au milieu", "espace entre les lignes", "création axiale limitée"],
        "metrics": {"width": 8, "central": 5, "security": 7, "pressing": 6, "transition": 7},
    },
    "3-5-2": {
        "build_up": "Supériorité numérique à la relance avec trois centraux et un milieu venant offrir une solution.",
        "attack": "Pistons très hauts, densité axiale et complémentarité de deux attaquants.",
        "defense": "Repli en 5-3-2 pour protéger la surface et fermer l’axe.",
        "transition": "Recherche rapide des pistons ou d’un attaquant décroché après récupération.",
        "strengths": ["densité axiale", "relance à trois", "deux attaquants"],
        "risks": ["espace derrière les pistons", "changements d’aile adverses", "forte dépense des pistons"],
        "metrics": {"width": 7, "central": 9, "security": 7, "pressing": 6, "transition": 8},
    },
    "3-4-3": {
        "build_up": "Relance à trois et occupation haute des couloirs pour étirer le premier rideau.",
        "attack": "Cinq joueurs peuvent occuper la dernière ligne avec les deux pistons.",
        "defense": "Pressing agressif mais repli nécessaire en ligne de cinq si la première pression est battue.",
        "transition": "Attaque immédiate des espaces avec trois joueurs offensifs.",
        "strengths": ["largeur maximale", "pression sur la relance", "nombreuses lignes de passe"],
        "risks": ["couloirs derrière les pistons", "défense exposée en transition", "duels des centraux"],
        "metrics": {"width": 9, "central": 7, "security": 5, "pressing": 8, "transition": 9},
    },
    "5-3-2": {
        "build_up": "Relance prudente avec appui des pistons et jeu direct possible sur les deux attaquants.",
        "attack": "Transitions et centres des pistons, avec peu de joueurs engagés entre les lignes.",
        "defense": "Protection forte de la surface, trois centraux et densité dans l’axe.",
        "transition": "Projection sélective pour conserver la sécurité derrière le ballon.",
        "strengths": ["défense de surface", "couverture axiale", "gestion d’un avantage"],
        "risks": ["bloc trop bas", "faible présence au pressing", "distance vers les attaquants"],
        "metrics": {"width": 5, "central": 8, "security": 9, "pressing": 4, "transition": 6},
    },
    "4-1-4-1": {
        "build_up": "Une sentinelle relie les centraux aux deux milieux relayeurs et sécurise les montées latérales.",
        "attack": "Cinq couloirs peuvent être occupés, mais l’attaquant doit être rejoint rapidement par les milieux.",
        "defense": "Bloc en 4-5-1 très dense au milieu, destiné à fermer les passes intérieures.",
        "transition": "Projection des relayeurs et ailiers autour d’un avant-centre souvent seul au départ.",
        "strengths": ["contrôle du milieu", "protection devant la défense", "largeur équilibrée"],
        "risks": ["attaquant isolé", "espace de chaque côté de la sentinelle", "manque de présence dans la surface"],
        "metrics": {"width": 7, "central": 9, "security": 8, "pressing": 7, "transition": 6},
    },
    "3-4-2-1": {
        "build_up": "Relance à trois avec deux milieux axiaux et des pistons disponibles très haut.",
        "attack": "Deux joueurs entre les lignes soutiennent l’avant-centre et libèrent les couloirs aux pistons.",
        "defense": "Repli en 5-4-1 ; la fermeture des demi-espaces dépend du retour des deux joueurs offensifs.",
        "transition": "Accélération immédiate par les deux meneurs intérieurs autour de la pointe.",
        "strengths": ["occupation des demi-espaces", "relance à trois", "soutien proche de l’attaquant"],
        "risks": ["dos des pistons", "largeur dépendante de deux joueurs", "milieu axial en sous-nombre"],
        "metrics": {"width": 8, "central": 8, "security": 6, "pressing": 7, "transition": 9},
    },
    "4-3-1-2": {
        "build_up": "Le milieu en losange multiplie les solutions axiales et permet aux latéraux de donner la largeur.",
        "attack": "Un meneur alimente deux attaquants proches, avec beaucoup de combinaisons dans l’axe.",
        "defense": "Densité centrale forte mais déplacements latéraux importants pour les relayeurs.",
        "transition": "Sorties verticales par le meneur ou directement vers le duo offensif.",
        "strengths": ["supériorité axiale", "deux attaquants", "présence entre les lignes"],
        "risks": ["couloirs exposés", "forte dépendance aux latéraux", "changements d’aile adverses"],
        "metrics": {"width": 4, "central": 10, "security": 7, "pressing": 7, "transition": 8},
    },
}

FORMATION_MATCHUPS = {
    ("4-3-3", "4-4-2"): (3.0, "Le milieu à trois peut créer un surnombre face aux deux milieux axiaux du 4-4-2."),
    ("4-4-2", "4-3-3"): (-3.0, "Le 4-4-2 risque une infériorité axiale, mais ses deux attaquants peuvent gêner la relance."),
    ("4-2-3-1", "4-4-2"): (2.5, "Le meneur peut recevoir dans le dos des deux milieux du 4-4-2."),
    ("4-4-2", "4-2-3-1"): (-2.5, "Le double pivot et le numéro 10 adverses peuvent contrôler l’axe."),
    ("3-5-2", "4-3-3"): (1.0, "Le 3-5-2 densifie l’axe, mais ses pistons seront ciblés par les ailiers."),
    ("4-3-3", "3-5-2"): (-1.0, "Les ailes offrent des espaces, mais le milieu peut subir la densité du 3-5-2."),
    ("3-4-3", "4-2-3-1"): (1.5, "La largeur du 3-4-3 peut repousser les ailiers, au prix d’espaces en transition."),
    ("4-2-3-1", "3-4-3"): (-1.5, "Le double pivot protège les transitions, mais peut être étiré par les pistons."),
    ("5-3-2", "4-3-3"): (-1.0, "Le 5-3-2 protège la surface mais peut concéder durablement les couloirs."),
    ("4-3-3", "5-3-2"): (1.0, "La largeur peut déplacer le bloc à cinq, avec un risque de contres dans le dos."),
}


def _ensure_schema():
    global _schema_ready
    if not _schema_ready:
        models.Base.metadata.create_all(bind=engine)
        _schema_ready = True


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _as_int(value):
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace("%", "").strip()))
    except (TypeError, ValueError):
        return None


def _as_float(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _api_error(response: dict) -> str | None:
    errors = response.get("errors")
    if not errors:
        return None
    if isinstance(errors, dict):
        return " ; ".join(f"{key}: {value}" for key, value in errors.items())
    if isinstance(errors, list):
        return " ; ".join(str(value) for value in errors)
    return str(errors)


def _ensure_player(session, player_data: dict):
    player_id = _as_int(player_data.get("id"))
    if player_id is None:
        return
    player = session.get(models.Player, player_id)
    if player is None:
        player = models.Player(
            id=player_id,
            name=player_data.get("name") or f"Joueur {player_id}",
            photo=player_data.get("photo"),
            raw_json=_dump(player_data),
            updated_at=_now(),
        )
    else:
        player.name = player_data.get("name") or player.name
        player.photo = player_data.get("photo") or player.photo
        player.updated_at = _now()
    session.add(player)


def _save_lineups(session, fixture_id: int, items: list[dict]) -> int:
    saved = 0
    now = _now()
    for item in items:
        team = item.get("team") or {}
        coach = item.get("coach") or {}
        team_id = _as_int(team.get("id"))
        if team_id is None:
            continue
        key = (int(fixture_id), team_id)
        row = session.get(models.FixtureLineup, key) or models.FixtureLineup(
            fixture_id=int(fixture_id), team_id=team_id
        )
        row.team_name = team.get("name")
        row.team_logo = team.get("logo")
        row.formation = item.get("formation")
        row.coach_id = _as_int(coach.get("id"))
        row.coach_name = coach.get("name")
        row.coach_photo = coach.get("photo")
        row.raw_json = _dump(item)
        row.updated_at = now
        session.add(row)

        session.query(models.FixtureLineupPlayer).filter_by(
            fixture_id=int(fixture_id), team_id=team_id
        ).delete(synchronize_session=False)
        for starter, collection in (
            (True, item.get("startXI") or []),
            (False, item.get("substitutes") or []),
        ):
            for entry in collection:
                player_data = entry.get("player") or entry
                player_id = _as_int(player_data.get("id"))
                if player_id is None:
                    continue
                _ensure_player(session, player_data)
                session.add(
                    models.FixtureLineupPlayer(
                        fixture_id=int(fixture_id),
                        team_id=team_id,
                        player_id=player_id,
                        player_name=player_data.get("name"),
                        number=_as_int(player_data.get("number")),
                        position=player_data.get("pos") or player_data.get("position"),
                        grid=player_data.get("grid"),
                        starter=starter,
                        raw_json=_dump(player_data),
                        updated_at=now,
                    )
                )
        saved += 1
    session.commit()
    return saved


def sync_lineups(fixture_id: int) -> dict:
    _ensure_schema()
    response = client.get_fixture_lineups(int(fixture_id))
    error = _api_error(response)
    if error:
        raise RuntimeError(error)
    items = response.get("response") or []
    with SessionLocal() as session:
        count = _save_lineups(session, int(fixture_id), items)
    return {"teams": count, "available": count > 0}


def _save_fixture_players(session, fixture_id: int, items: list[dict]) -> int:
    now = _now()
    saved = 0
    for team_payload in items:
        team = team_payload.get("team") or {}
        team_id = _as_int(team.get("id"))
        if team_id is None:
            continue
        for item in team_payload.get("players") or []:
            player_data = item.get("player") or {}
            player_id = _as_int(player_data.get("id"))
            statistics = (item.get("statistics") or [{}])[0] or {}
            if player_id is None:
                continue
            _ensure_player(session, player_data)
            games = statistics.get("games") or {}
            shots = statistics.get("shots") or {}
            goals = statistics.get("goals") or {}
            passes = statistics.get("passes") or {}
            tackles = statistics.get("tackles") or {}
            duels = statistics.get("duels") or {}
            dribbles = statistics.get("dribbles") or {}
            fouls = statistics.get("fouls") or {}
            cards = statistics.get("cards") or {}
            penalty = statistics.get("penalty") or {}
            key = (int(fixture_id), team_id, player_id)
            row = session.get(models.FixturePlayerStatistic, key)
            if row is None:
                row = models.FixturePlayerStatistic(
                    fixture_id=int(fixture_id), team_id=team_id, player_id=player_id
                )
            values = {
                "player_name": player_data.get("name"),
                "player_photo": player_data.get("photo"),
                "minutes": _as_int(games.get("minutes")),
                "number": _as_int(games.get("number")),
                "position": games.get("position"),
                "rating": _as_float(games.get("rating")),
                "captain": games.get("captain"),
                "substitute": games.get("substitute"),
                "offsides": _as_int(statistics.get("offsides")),
                "shots_total": _as_int(shots.get("total")),
                "shots_on": _as_int(shots.get("on")),
                "goals_total": _as_int(goals.get("total")),
                "goals_conceded": _as_int(goals.get("conceded")),
                "goals_assists": _as_int(goals.get("assists")),
                "goals_saves": _as_int(goals.get("saves")),
                "passes_total": _as_int(passes.get("total")),
                "passes_key": _as_int(passes.get("key")),
                "passes_accuracy": _as_int(passes.get("accuracy")),
                "tackles_total": _as_int(tackles.get("total")),
                "tackles_blocks": _as_int(tackles.get("blocks")),
                "tackles_interceptions": _as_int(tackles.get("interceptions")),
                "duels_total": _as_int(duels.get("total")),
                "duels_won": _as_int(duels.get("won")),
                "dribbles_attempts": _as_int(dribbles.get("attempts")),
                "dribbles_success": _as_int(dribbles.get("success")),
                "dribbles_past": _as_int(dribbles.get("past")),
                "fouls_drawn": _as_int(fouls.get("drawn")),
                "fouls_committed": _as_int(fouls.get("committed")),
                "cards_yellow": _as_int(cards.get("yellow")),
                "cards_red": _as_int(cards.get("red")),
                "penalty_won": _as_int(penalty.get("won")),
                "penalty_committed": _as_int(penalty.get("commited")),
                "penalty_scored": _as_int(penalty.get("scored")),
                "penalty_missed": _as_int(penalty.get("missed")),
                "penalty_saved": _as_int(penalty.get("saved")),
                "raw_json": _dump(statistics),
                "updated_at": now,
            }
            for field, value in values.items():
                setattr(row, field, value)
            session.add(row)
            saved += 1
    return saved


def sync_fixture_players(fixture_id: int, force: bool = False) -> dict:
    _ensure_schema()
    with SessionLocal() as session:
        cached = session.get(models.FixturePlayerSync, int(fixture_id))
        if cached is not None and cached.status == "available" and not force:
            return {"players": cached.player_count, "cached": True, "available": True}

    try:
        response = client.get_fixture_players(int(fixture_id))
        error = _api_error(response)
        if error:
            raise RuntimeError(error)
        items = response.get("response") or []
        with SessionLocal() as session:
            count = _save_fixture_players(session, int(fixture_id), items)
            sync = session.get(models.FixturePlayerSync, int(fixture_id))
            if sync is None:
                sync = models.FixturePlayerSync(fixture_id=int(fixture_id), status="unavailable")
            sync.status = "available" if count else "unavailable"
            sync.player_count = count
            sync.error = None
            sync.updated_at = _now()
            session.add(sync)
            session.commit()
        return {"players": count, "cached": False, "available": count > 0}
    except Exception as exc:
        with SessionLocal() as session:
            sync = session.get(models.FixturePlayerSync, int(fixture_id))
            if sync is None:
                sync = models.FixturePlayerSync(fixture_id=int(fixture_id), status="error")
            sync.status = "error"
            sync.error = str(exc)
            sync.updated_at = _now()
            session.add(sync)
            session.commit()
        raise


def _recent_fixture_ids(team_id: int, before_date, limit: int = 5) -> list[int]:
    query = text(
        """
        SELECT fixture_id
        FROM matches
        WHERE (home_team_id = :team_id OR away_team_id = :team_id)
          AND date < :before_date
          AND home_goals IS NOT NULL AND away_goals IS NOT NULL
        ORDER BY date DESC
        LIMIT :limit
        """
    )
    try:
        rows = pd.read_sql(
            query,
            engine,
            params={"team_id": int(team_id), "before_date": str(before_date), "limit": int(limit)},
        )
        return [int(value) for value in rows["fixture_id"].tolist()]
    except Exception:
        return []


def sync_match_intelligence(
    fixture_id: int,
    home_team_id: int,
    away_team_id: int,
    season: int,
    match_date,
    recent_limit: int = 5,
    progress_callback=None,
) -> dict:
    """Synchronise composition, effectifs de saison et performances récentes."""
    from services import player_service

    _ensure_schema()
    result = {
        "lineups": {"available": False, "teams": 0},
        "season_players": 0,
        "recent_fixtures": 0,
        "recent_players": 0,
        "errors": [],
    }
    league_id = _fixture_league_id(int(fixture_id))

    def progress(current: int, total: int, label: str):
        if progress_callback:
            progress_callback(current, max(1, total), label)

    progress(0, 100, "Préparation des compositions et performances")
    try:
        result["lineups"] = sync_lineups(int(fixture_id))
    except Exception as exc:
        result["errors"].append(f"Compositions : {exc}")
    progress(15, 100, "Composition du match vérifiée")

    for team_index, team_id in enumerate((int(home_team_id), int(away_team_id))):
        team_start = 15 + team_index * 15
        with engine.begin() as conn:
            existing = int(
                conn.execute(
                    text(
                        "SELECT COUNT(*) FROM player_statistics "
                        "WHERE team_id = :team_id AND league_id = :league_id AND season = :season"
                    ),
                    {"team_id": team_id, "league_id": league_id, "season": int(season)},
                ).scalar()
                or 0
            )
        if existing == 0:
            try:
                synced = player_service.sync_players(
                    league_id=league_id,
                    season=int(season),
                    team_id=team_id,
                    progress_callback=lambda current, total, label, start=team_start: progress(
                        start + int((current / max(1, total)) * 15),
                        100,
                        label,
                    ),
                )
                result["season_players"] += int(synced.get("profiles") or 0)
            except Exception as exc:
                result["errors"].append(f"Effectif {team_id} : {exc}")
        progress(team_start + 15, 100, f"Effectif de l’équipe {team_id} vérifié")

    fixture_ids = []
    for team_id in (int(home_team_id), int(away_team_id)):
        fixture_ids.extend(_recent_fixture_ids(team_id, match_date, recent_limit))
    fixture_ids = list(dict.fromkeys(fixture_ids))
    result["recent_fixtures"] = len(fixture_ids)
    total_recent = max(1, len(fixture_ids))
    for fixture_index, recent_fixture_id in enumerate(fixture_ids, start=1):
        try:
            saved = sync_fixture_players(recent_fixture_id)
            result["recent_players"] += int(saved.get("players") or 0)
        except Exception as exc:
            result["errors"].append(f"Match {recent_fixture_id} : {exc}")
        progress(
            45 + int((fixture_index / total_recent) * 55),
            100,
            f"Performance récente {fixture_index}/{len(fixture_ids)} enregistrée",
        )
        time.sleep(0.35)
    progress(100, 100, "Compositions et forme des joueurs synchronisées")
    return result


def _fixture_league_id(fixture_id: int) -> int:
    with engine.begin() as conn:
        value = conn.execute(
            text("SELECT league_id FROM matches WHERE fixture_id = :fixture_id"),
            {"fixture_id": int(fixture_id)},
        ).scalar_one_or_none()
    if value is None:
        raise RuntimeError("Championnat introuvable pour cette rencontre.")
    return int(value)


def _strategy(formation: str | None) -> str:
    if not formation:
        return "Organisation tactique indisponible."
    return FORMATION_PLANS.get(
        formation,
        "Organisation estimée à partir du dispositif ; les consignes réelles de l’entraîneur restent inconnues.",
    )


def formation_profile(formation: str | None) -> dict:
    formation = str(formation or "")
    profile = FORMATION_PROFILES.get(formation)
    if profile:
        return {"formation": formation, **profile}
    return {
        "formation": formation or "Indisponible",
        "build_up": "Relance impossible à caractériser sans dispositif fiable.",
        "attack": "Animation offensive non déterminée.",
        "defense": "Organisation défensive non déterminée.",
        "transition": "Transitions non déterminées.",
        "strengths": [],
        "risks": ["données tactiques insuffisantes"],
        "metrics": {"width": 5, "central": 5, "security": 5, "pressing": 5, "transition": 5},
    }


def _official_lineup(fixture_id: int, team_id: int) -> dict | None:
    try:
        lineup = pd.read_sql(
            text(
                "SELECT * FROM fixture_lineups "
                "WHERE fixture_id = :fixture_id AND team_id = :team_id"
            ),
            engine,
            params={"fixture_id": int(fixture_id), "team_id": int(team_id)},
        )
        players = pd.read_sql(
            text(
                "SELECT * FROM fixture_lineup_players "
                "WHERE fixture_id = :fixture_id AND team_id = :team_id "
                "ORDER BY starter DESC, grid, number"
            ),
            engine,
            params={"fixture_id": int(fixture_id), "team_id": int(team_id)},
        )
    except Exception:
        return None
    if lineup.empty or players.empty:
        return None
    info = lineup.iloc[0]
    return {
        "team_id": int(team_id),
        "team_name": info.get("team_name"),
        "team_logo": info.get("team_logo"),
        "formation": info.get("formation"),
        "coach_name": info.get("coach_name"),
        "official": True,
        "formation_source": "composition officielle du match",
        "starters": players[players["starter"] == 1].to_dict("records"),
        "substitutes": players[players["starter"] == 0].to_dict("records"),
    }


def _formation_targets(formation: str | None) -> dict[str, int]:
    normalized = str(formation or "")
    known = {
        "4-3-3": (4, 3, 3),
        "4-2-3-1": (4, 5, 1),
        "4-4-2": (4, 4, 2),
        "3-5-2": (3, 5, 2),
        "3-4-3": (3, 4, 3),
        "5-3-2": (5, 3, 2),
        "4-1-4-1": (4, 5, 1),
        "3-4-2-1": (3, 6, 1),
        "4-3-1-2": (4, 4, 2),
    }
    defenders, midfielders, attackers = known.get(normalized, (4, 5, 1))
    return {
        "Goalkeeper": 1,
        "Defender": defenders,
        "Midfielder": midfielders,
        "Attacker": attackers,
    }


def _choose_projected_players(
    frame: pd.DataFrame,
    preferred_formation: str | None = None,
) -> tuple[str, list[dict]]:
    if frame.empty:
        return "", []
    frame = frame.copy()
    frame["games_lineups"] = pd.to_numeric(frame["games_lineups"], errors="coerce").fillna(0)
    frame["games_minutes"] = pd.to_numeric(frame["games_minutes"], errors="coerce").fillna(0)
    frame["recent_starts"] = pd.to_numeric(frame.get("recent_starts"), errors="coerce").fillna(0)
    frame["recent_minutes_observed"] = pd.to_numeric(
        frame.get("recent_minutes_observed"), errors="coerce"
    ).fillna(0)
    frame["recent_rating_observed"] = pd.to_numeric(
        frame.get("recent_rating_observed"), errors="coerce"
    ).fillna(0)
    frame["selection_score"] = (
        frame["games_lineups"] * 2.0
        + frame["games_minutes"] / 90.0
        + frame["recent_starts"] * 4.0
        + frame["recent_minutes_observed"] / 90.0
        + frame["recent_rating_observed"]
    )
    frame = frame.sort_values(
        ["selection_score", "recent_starts", "games_lineups", "games_minutes"],
        ascending=False,
    )
    groups = {
        position: frame[frame["games_position"] == position].to_dict("records")
        for position in ("Goalkeeper", "Defender", "Midfielder", "Attacker")
    }
    if preferred_formation:
        formation = str(preferred_formation)
    elif len(groups["Attacker"]) >= 3:
        formation = "4-3-3"
    elif len(groups["Attacker"]) >= 2 and len(groups["Midfielder"]) >= 4:
        formation = "4-4-2"
    else:
        formation = "4-2-3-1"
    targets = _formation_targets(formation)

    selected = []
    selected_ids = set()
    for position, count in targets.items():
        for player in groups[position][:count]:
            selected.append(player)
            selected_ids.add(int(player["player_id"]))
    # Never pad a projection with players from an unrelated position just to
    # reach eleven.  Returning an incomplete projection is more honest and
    # lets the UI explain exactly which positions lack evidence.
    return formation, selected[:11]


def _inferred_formation(defenders: int, midfielders: int, attackers: int) -> str | None:
    mapping = {
        (4, 3, 3): "4-3-3",
        (4, 4, 2): "4-4-2",
        (4, 5, 1): "4-2-3-1",
        (3, 5, 2): "3-5-2",
        (3, 4, 3): "3-4-3",
        (5, 3, 2): "5-3-2",
        (3, 6, 1): "3-4-2-1",
    }
    return mapping.get((int(defenders), int(midfielders), int(attackers)))


def _historical_formation(
    team_id: int,
    league_id: int,
    season: int,
    before_date,
) -> dict:
    observations = []
    try:
        official = pd.read_sql(
            text(
                """
                SELECT fl.formation, m.date, m.season, 'official' AS source
                FROM fixture_lineups fl
                JOIN matches m ON m.fixture_id = fl.fixture_id
                WHERE fl.team_id = :team_id
                  AND m.league_id = :league_id
                  AND m.season <= :season
                  AND m.date < :before_date
                  AND fl.formation IS NOT NULL
                ORDER BY m.date DESC
                LIMIT 30
                """
            ),
            engine,
            params={
                "team_id": int(team_id),
                "league_id": int(league_id),
                "season": int(season),
                "before_date": str(before_date),
            },
        )
        observations.extend(official.to_dict("records"))
    except Exception:
        pass
    try:
        inferred = pd.read_sql(
            text(
                """
                SELECT fps.fixture_id, m.date, m.season,
                       SUM(CASE WHEN fps.position = 'D' THEN 1 ELSE 0 END) AS defenders,
                       SUM(CASE WHEN fps.position = 'M' THEN 1 ELSE 0 END) AS midfielders,
                       SUM(CASE WHEN fps.position = 'F' THEN 1 ELSE 0 END) AS attackers
                FROM fixture_player_statistics fps
                JOIN matches m ON m.fixture_id = fps.fixture_id
                WHERE fps.team_id = :team_id
                  AND m.league_id = :league_id
                  AND m.season <= :season
                  AND m.date < :before_date
                  AND COALESCE(fps.substitute, 0) = 0
                GROUP BY fps.fixture_id, m.date, m.season
                HAVING COUNT(*) >= 10
                ORDER BY m.date DESC
                LIMIT 30
                """
            ),
            engine,
            params={
                "team_id": int(team_id),
                "league_id": int(league_id),
                "season": int(season),
                "before_date": str(before_date),
            },
        )
        for row in inferred.to_dict("records"):
            formation = _inferred_formation(
                row["defenders"], row["midfielders"], row["attackers"]
            )
            if formation:
                observations.append(
                    {
                        "formation": formation,
                        "date": row["date"],
                        "season": row["season"],
                        "source": "inferred",
                    }
                )
    except Exception:
        pass

    scores = defaultdict(float)
    uses = defaultdict(int)
    sources = defaultdict(set)
    ordered = sorted(
        observations,
        key=lambda row: str(row.get("date") or ""),
        reverse=True,
    )
    for index, row in enumerate(ordered):
        formation = str(row.get("formation") or "")
        if not formation:
            continue
        recency_weight = 1.0 / (1.0 + index * 0.12)
        season_weight = 1.2 if int(row.get("season") or 0) == int(season) else 1.0
        source_weight = 1.25 if row.get("source") == "official" else 1.0
        scores[formation] += recency_weight * season_weight * source_weight
        uses[formation] += 1
        sources[formation].add(str(row.get("source")))
    if not scores:
        return {
            "formation": None,
            "confidence": 0.0,
            "sample_size": 0,
            "history": [],
            "source": "répartition des joueurs disponibles",
        }
    ranked = sorted(scores, key=scores.get, reverse=True)
    winner = ranked[0]
    total_score = sum(scores.values())
    confidence = scores[winner] / max(0.001, total_score)
    history = [
        {
            "formation": formation,
            "uses": uses[formation],
            "weight": round(scores[formation], 2),
        }
        for formation in ranked[:4]
    ]
    source_label = (
        "compositions officielles et titulaires observés"
        if "official" in sources[winner]
        else "titulaires observés lors des matchs précédents"
    )
    return {
        "formation": winner,
        "confidence": round(confidence, 3),
        "sample_size": sum(uses.values()),
        "history": history,
        "source": source_label,
    }


def _projected_player_pool(
    team_id: int,
    league_id: int,
    season: int,
    before_date,
) -> tuple[pd.DataFrame, int | None]:
    source_league_id = int(league_id)
    try:
        source = pd.read_sql(
            text(
                """
                SELECT league_id, season
                FROM player_statistics
                WHERE team_id = :team_id AND league_id = :league_id
                  AND season <= :season
                GROUP BY league_id, season
                ORDER BY season DESC, COUNT(*) DESC
                LIMIT 1
                """
            ),
            engine,
            params={"team_id": int(team_id), "league_id": int(league_id), "season": int(season)},
        )
        if source.empty:
            source = pd.read_sql(
                text(
                    """
                    SELECT league_id, season
                    FROM player_statistics
                    WHERE team_id = :team_id AND season <= :season
                    GROUP BY league_id, season
                    ORDER BY season DESC, COUNT(*) DESC
                    LIMIT 1
                    """
                ),
                engine,
                params={"team_id": int(team_id), "season": int(season)},
            )
        if source.empty:
            source_season = None
        else:
            source_season = int(source.iloc[0]["season"])
            source_league_id = int(source.iloc[0]["league_id"])
    except Exception:
        source_season = None

    if source_season is None:
        frame = pd.DataFrame()
    else:
        frame = pd.read_sql(
            text(
                """
                SELECT
                    ps.player_id, p.name AS player_name, p.photo AS player_photo,
                    p.injured, ps.team_name, ps.team_logo,
                    ps.games_number AS number, ps.games_position AS position,
                    ps.games_position, ps.games_rating, ps.games_lineups,
                    ps.games_minutes, ps.goals_total, ps.goals_assists
                FROM player_statistics ps
                JOIN players p ON p.id = ps.player_id
                WHERE ps.team_id = :team_id AND ps.league_id = :source_league_id
                  AND ps.season = :source_season
                """
            ),
            engine,
            params={
                "team_id": int(team_id),
                "source_league_id": int(source_league_id),
                "source_season": int(source_season),
            },
        )

    try:
        recent = pd.read_sql(
            text(
                """
                SELECT fps.player_id, MAX(fps.player_name) AS recent_player_name,
                       MAX(fps.player_photo) AS recent_player_photo,
                       MAX(fps.number) AS recent_number,
                       MAX(fps.position) AS recent_position,
                       SUM(CASE WHEN COALESCE(fps.substitute, 0) = 0 THEN 1 ELSE 0 END) AS recent_starts,
                       SUM(COALESCE(fps.minutes, 0)) AS recent_minutes_observed,
                       AVG(fps.rating) AS recent_rating_observed,
                       MAX(m.date) AS last_seen
                FROM fixture_player_statistics fps
                JOIN matches m ON m.fixture_id = fps.fixture_id
                WHERE fps.team_id = :team_id
                  AND m.league_id = :league_id
                  AND m.date < :before_date
                  AND m.fixture_id IN (
                      SELECT recent_matches.fixture_id
                      FROM (
                          SELECT m2.fixture_id
                          FROM matches m2
                          WHERE (m2.home_team_id = :team_id OR m2.away_team_id = :team_id)
                            AND m2.date < :before_date
                          ORDER BY m2.date DESC
                          LIMIT 10
                      ) AS recent_matches
                  )
                GROUP BY fps.player_id
                """
            ),
            engine,
            params={
                "team_id": int(team_id),
                "league_id": int(league_id),
                "before_date": str(before_date),
            },
        )
    except Exception:
        recent = pd.DataFrame()

    if frame.empty and recent.empty:
        try:
            historical_lineups = pd.read_sql(
                text(
                    """
                    SELECT flp.player_id, MAX(flp.player_name) AS player_name,
                           MAX(flp.number) AS number, MAX(flp.position) AS position,
                           SUM(CASE WHEN flp.starter = 1 THEN 1 ELSE 0 END) AS recent_starts,
                           MAX(m.date) AS last_seen
                    FROM fixture_lineup_players flp
                    JOIN matches m ON m.fixture_id = flp.fixture_id
                    WHERE flp.team_id = :team_id
                      AND m.date < :before_date
                    GROUP BY flp.player_id
                    ORDER BY recent_starts DESC, last_seen DESC
                    """
                ),
                engine,
                params={"team_id": int(team_id), "before_date": str(before_date)},
            )
            if not historical_lineups.empty:
                historical_lineups["games_position"] = historical_lineups["position"].map(
                    {"G": "Goalkeeper", "D": "Defender", "M": "Midfielder", "F": "Attacker"}
                )
                historical_lineups["player_photo"] = None
                historical_lineups["injured"] = 0
                historical_lineups["team_name"] = None
                historical_lineups["team_logo"] = None
                historical_lineups["games_rating"] = None
                historical_lineups["games_lineups"] = historical_lineups["recent_starts"]
                historical_lineups["games_minutes"] = 0
                historical_lineups["goals_total"] = 0
                historical_lineups["goals_assists"] = 0
                historical_lineups["recent_minutes_observed"] = 0
                historical_lineups["recent_rating_observed"] = 0
                frame = historical_lineups
        except Exception:
            pass

    if frame.empty and recent.empty:
        return pd.DataFrame(), source_season
    if frame.empty:
        frame = recent.rename(
            columns={
                "recent_player_name": "player_name",
                "recent_player_photo": "player_photo",
                "recent_number": "number",
                "recent_position": "position",
            }
        )
        frame["games_position"] = frame["position"].map(
            {"G": "Goalkeeper", "D": "Defender", "M": "Midfielder", "F": "Attacker"}
        )
        for column in (
            "injured", "team_name", "team_logo", "games_rating",
            "games_lineups", "games_minutes", "goals_total", "goals_assists",
        ):
            frame[column] = 0 if column not in {"team_name", "team_logo"} else None
    elif not recent.empty:
        frame = frame.merge(recent, on="player_id", how="outer")
        frame["player_name"] = frame["player_name"].fillna(frame["recent_player_name"])
        frame["player_photo"] = frame["player_photo"].fillna(frame["recent_player_photo"])
        frame["number"] = frame["number"].fillna(frame["recent_number"])
        frame["position"] = frame["position"].fillna(
            frame["recent_position"].map(
                {"G": "Goalkeeper", "D": "Defender", "M": "Midfielder", "F": "Attacker"}
            )
        )
        frame["games_position"] = frame["games_position"].fillna(frame["position"])

    for column in ("recent_starts", "recent_minutes_observed", "recent_rating_observed"):
        if column not in frame:
            frame[column] = 0
    if "injured" in frame:
        frame = frame[pd.to_numeric(frame["injured"], errors="coerce").fillna(0) == 0]
    return frame, source_season


def _projected_lineup(
    team_id: int,
    league_id: int,
    season: int,
    before_date=None,
) -> dict | None:
    before_date = before_date or _now()
    try:
        frame, source_season = _projected_player_pool(
            team_id, league_id, season, before_date
        )
    except Exception:
        return None
    formation_stats = _historical_formation(team_id, league_id, season, before_date)
    formation, starters = _choose_projected_players(
        frame, formation_stats.get("formation")
    )
    if not starters:
        return None
    selected_ids = {int(player["player_id"]) for player in starters}
    targets = _formation_targets(formation)
    observed_counts = defaultdict(int)
    for player in starters:
        observed_counts[str(player.get("games_position") or "")] += 1
    missing_positions = {
        position: max(0, int(required) - observed_counts.get(position, 0))
        for position, required in targets.items()
        if observed_counts.get(position, 0) < int(required)
    }
    lineup_complete = len(starters) == 11 and not missing_positions
    substitutes = [
        player for player in frame.to_dict("records")
        if int(player["player_id"]) not in selected_ids
    ][:9]
    try:
        team_row = pd.read_sql(
            text("SELECT name, logo FROM teams WHERE id = :team_id"),
            engine,
            params={"team_id": int(team_id)},
        )
    except Exception:
        team_row = pd.DataFrame()
    team_name = starters[0].get("team_name")
    team_logo = starters[0].get("team_logo")
    if pd.isna(team_name):
        team_name = None
    if pd.isna(team_logo):
        team_logo = None
    if not team_row.empty:
        team_name = team_name or team_row.iloc[0]["name"]
        team_logo = team_logo or team_row.iloc[0]["logo"]
    player_coverage = min(1.0, len(starters) / 11)
    recent_coverage = min(
        1.0,
        sum(1 for player in starters if float(player.get("recent_starts") or 0) > 0) / 11,
    )
    season_gap = max(0, int(season) - int(source_season or season))
    freshness = max(0.45, 1.0 - season_gap * 0.2)
    confidence = (
        0.45 * float(formation_stats.get("confidence") or 0.35)
        + 0.35 * recent_coverage
        + 0.20 * player_coverage
    ) * freshness
    return {
        "team_id": int(team_id),
        "team_name": team_name,
        "team_logo": team_logo,
        "formation": formation,
        "formation_source": (
            f"{formation_stats['source']} · {formation_stats['sample_size']} match(s) étudié(s)"
            if formation_stats.get("sample_size")
            else "répartition statistique de l’effectif disponible"
        ),
        "formation_history": formation_stats.get("history") or [],
        "formation_confidence": float(formation_stats.get("confidence") or 0.35),
        "projection_confidence": round(max(0.15, min(0.95, confidence)), 3),
        "player_source_season": source_season,
        "player_source": (
            f"effectif {source_season} et matchs récents"
            if source_season is not None
            else "joueurs observés lors des matchs récents"
        ),
        "coach_name": None,
        "official": False,
        "complete": lineup_complete,
        "status": "projection complète" if lineup_complete else "projection incomplète",
        "missing_positions": missing_positions,
        "starters": starters,
        "substitutes": substitutes,
    }


def _recent_player_form(team_id: int, before_date, limit: int = 5) -> dict[int, dict]:
    fixture_ids = _recent_fixture_ids(team_id, before_date, limit)
    if not fixture_ids:
        return {}
    placeholders = ",".join(f":fixture_{index}" for index in range(len(fixture_ids)))
    params = {"team_id": int(team_id)}
    params.update({f"fixture_{index}": value for index, value in enumerate(fixture_ids)})
    try:
        frame = pd.read_sql(
            text(
                f"""
                SELECT fps.*, m.date
                FROM fixture_player_statistics fps
                JOIN matches m ON m.fixture_id = fps.fixture_id
                WHERE fps.team_id = :team_id
                  AND fps.fixture_id IN ({placeholders})
                ORDER BY m.date DESC
                """
            ),
            engine,
            params=params,
        )
    except Exception:
        return {}
    result = {}
    for player_id, group in frame.groupby("player_id"):
        ratings = pd.to_numeric(group["rating"], errors="coerce").dropna()
        result[int(player_id)] = {
            "recent_rating": round(float(ratings.mean()), 2) if not ratings.empty else None,
            "recent_matches": int(len(ratings)),
            "recent_minutes": int(pd.to_numeric(group["minutes"], errors="coerce").fillna(0).sum()),
            "recent_goals": int(pd.to_numeric(group["goals_total"], errors="coerce").fillna(0).sum()),
            "recent_assists": int(pd.to_numeric(group["goals_assists"], errors="coerce").fillna(0).sum()),
        }
    return result


def _position_group(value) -> str:
    labels = {
        "G": "goalkeeper",
        "Goalkeeper": "goalkeeper",
        "D": "defense",
        "Defender": "defense",
        "M": "midfield",
        "Midfielder": "midfield",
        "F": "attack",
        "Attacker": "attack",
    }
    return labels.get(str(value or ""), "other")


def _enrich_lineup(
    lineup: dict | None,
    league_id: int,
    season: int,
    before_date,
) -> dict | None:
    if lineup is None:
        return None
    team_id = int(lineup["team_id"])
    recent = _recent_player_form(team_id, before_date)
    try:
        season_rows = pd.read_sql(
            text(
                "SELECT player_id, games_rating FROM player_statistics "
                "WHERE team_id = :team_id AND league_id = :league_id AND season = :season"
            ),
            engine,
            params={
                "team_id": team_id,
                "league_id": int(league_id),
                "season": int(season),
            },
        )
        season_ratings = {
            int(row.player_id): _as_float(row.games_rating)
            for row in season_rows.itertuples()
        }
    except Exception:
        season_ratings = {}

    starter_ratings = []
    line_values = defaultdict(list)
    recent_starters = 0
    recent_depth = []
    for collection_name in ("starters", "substitutes"):
        enriched = []
        for player in lineup[collection_name]:
            player = dict(player)
            player_id = int(player["player_id"])
            form = recent.get(player_id, {})
            rating = form.get("recent_rating")
            if rating is None:
                rating = _as_float(player.get("games_rating")) or season_ratings.get(player_id)
                player["form_source"] = "saison" if rating is not None else "indisponible"
            else:
                player["form_source"] = "5 derniers matchs"
            player.update(form)
            player["form_rating"] = rating
            if collection_name == "starters" and rating is not None:
                starter_ratings.append(float(rating))
                line_values[
                    _position_group(player.get("position") or player.get("games_position"))
                ].append(float(rating))
                if form.get("recent_matches"):
                    recent_starters += 1
                    recent_depth.append(int(form["recent_matches"]))
            enriched.append(player)
        lineup[collection_name] = enriched

    average = sum(starter_ratings) / len(starter_ratings) if starter_ratings else 6.5
    form_score = max(20.0, min(80.0, 50.0 + (average - 6.5) * 22.0))
    starter_count = max(1, len(lineup["starters"]))
    recent_coverage = recent_starters / starter_count
    if recent_starters:
        depth = min(1.0, (sum(recent_depth) / len(recent_depth)) / 3)
        reliability = recent_coverage * (0.4 + 0.6 * depth)
        source = "performances des 5 derniers matchs"
    else:
        rating_coverage = len(starter_ratings) / starter_count
        reliability = rating_coverage * 0.25
        source = "moyennes de la saison"
    lineup["average_rating"] = round(average, 2)
    lineup["form_score"] = round(form_score, 1)
    lineup["form_reliability"] = round(max(0.0, min(1.0, reliability)), 3)
    lineup["form_source"] = source
    composition_reliability = (
        1.0
        if lineup.get("official")
        else float(lineup.get("projection_confidence") or 0.35)
    )
    lineup["intelligence_reliability"] = round(
        max(0.0, min(1.0, 0.55 * reliability + 0.45 * composition_reliability)),
        3,
    )
    lineup["strategy"] = _strategy(lineup.get("formation"))
    lineup["tactical_profile"] = formation_profile(lineup.get("formation"))
    lineup["strategy_evidence"] = [
        {
            **item,
            "strategy": _strategy(item.get("formation")),
        }
        for item in (lineup.get("formation_history") or [])
    ]
    lineup["line_ratings"] = {
        line: round(sum(values) / len(values), 2) if values else round(average, 2)
        for line, values in {
            "goalkeeper": line_values["goalkeeper"],
            "defense": line_values["defense"],
            "midfield": line_values["midfield"],
            "attack": line_values["attack"],
        }.items()
    }
    return lineup


def _persist_projected_lineup(
    scope_key: str,
    lineup: dict,
    league_id: int,
    season: int,
    fixture_id: int | None = None,
) -> None:
    if not lineup or lineup.get("official"):
        return
    _ensure_projected_lineups_table()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO projected_lineups (
                    scope_key, fixture_id, team_id, league_id, season, formation,
                    confidence, formation_source, player_source, strategy,
                    lineup_json, updated_at
                ) VALUES (
                    :scope_key, :fixture_id, :team_id, :league_id, :season, :formation,
                    :confidence, :formation_source, :player_source, :strategy,
                    :lineup_json, :updated_at
                )
                ON CONFLICT(scope_key) DO UPDATE SET
                    fixture_id = excluded.fixture_id,
                    team_id = excluded.team_id,
                    league_id = excluded.league_id,
                    season = excluded.season,
                    formation = excluded.formation,
                    confidence = excluded.confidence,
                    formation_source = excluded.formation_source,
                    player_source = excluded.player_source,
                    strategy = excluded.strategy,
                    lineup_json = excluded.lineup_json,
                    updated_at = excluded.updated_at
                """
            ),
            {
                "scope_key": str(scope_key),
                "fixture_id": int(fixture_id) if fixture_id is not None else None,
                "team_id": int(lineup["team_id"]),
                "league_id": int(league_id),
                "season": int(season),
                "formation": lineup.get("formation"),
                "confidence": float(lineup.get("projection_confidence") or 0),
                "formation_source": lineup.get("formation_source"),
                "player_source": lineup.get("player_source"),
                "strategy": lineup.get("strategy"),
                "lineup_json": json.dumps(lineup, ensure_ascii=False, default=str),
                "updated_at": _now().isoformat(),
            },
        )


def _load_projected_lineup(scope_key: str) -> dict | None:
    _ensure_projected_lineups_table()
    try:
        with engine.begin() as conn:
            raw = conn.execute(
                text(
                    "SELECT lineup_json FROM projected_lineups "
                    "WHERE scope_key = :scope_key"
                ),
                {"scope_key": str(scope_key)},
            ).scalar_one_or_none()
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _ensure_projected_lineups_table() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS projected_lineups (
                    scope_key TEXT PRIMARY KEY,
                    fixture_id INTEGER,
                    team_id INTEGER NOT NULL,
                    league_id INTEGER NOT NULL,
                    season INTEGER NOT NULL,
                    formation TEXT,
                    confidence REAL NOT NULL DEFAULT 0,
                    formation_source TEXT,
                    player_source TEXT,
                    strategy TEXT,
                    lineup_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_projected_lineups_fixture_id "
                "ON projected_lineups (fixture_id)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_projected_lineups_team_id "
                "ON projected_lineups (team_id)"
            )
        )


def analyze_tactical_matchup(home: dict | None, away: dict | None) -> dict | None:
    if not home or not away:
        return None
    home_formation = str(home.get("formation") or "")
    away_formation = str(away.get("formation") or "")
    home_profile = formation_profile(home_formation)
    away_profile = formation_profile(away_formation)
    structural_edge, structural_reading = FORMATION_MATCHUPS.get(
        (home_formation, away_formation),
        (
            0.0,
            "Aucun avantage structurel automatique : l’exécution, les déplacements "
            "et les duels individuels devraient départager les dispositifs.",
        ),
    )
    home_lines = home.get("line_ratings") or {}
    away_lines = away.get("line_ratings") or {}
    home_attack = float(home_lines.get("attack") or home.get("average_rating") or 6.5)
    home_midfield = float(home_lines.get("midfield") or home.get("average_rating") or 6.5)
    home_defense = float(home_lines.get("defense") or home.get("average_rating") or 6.5)
    away_attack = float(away_lines.get("attack") or away.get("average_rating") or 6.5)
    away_midfield = float(away_lines.get("midfield") or away.get("average_rating") or 6.5)
    away_defense = float(away_lines.get("defense") or away.get("average_rating") or 6.5)

    home_threat = 0.65 * (home_attack - away_defense) + 0.35 * (home_midfield - away_midfield)
    away_threat = 0.65 * (away_attack - home_defense) + 0.35 * (away_midfield - home_midfield)
    line_edge = max(-8.0, min(8.0, (home_threat - away_threat) * 2.2))
    hm = home_profile["metrics"]
    am = away_profile["metrics"]
    metric_edge = (
        ((hm["transition"] - am["security"]) - (am["transition"] - hm["security"])) * 0.35
        + (hm["pressing"] - am["pressing"]) * 0.15
        + (hm["central"] - am["central"]) * 0.10
    )
    metric_edge = max(-3.0, min(3.0, metric_edge))
    edge = max(-15.0, min(15.0, structural_edge + line_edge + metric_edge))

    home_advantages = []
    away_advantages = []
    if home_attack > away_defense + 0.2:
        home_advantages.append("la ligne offensive présente une meilleure note que la défense adverse")
    elif away_defense > home_attack + 0.2:
        away_advantages.append("la défense paraît capable de contenir la ligne offensive adverse")
    if home_midfield > away_midfield + 0.15:
        home_advantages.append("le milieu possède un avantage qualitatif pour contrôler les deuxièmes ballons")
    elif away_midfield > home_midfield + 0.15:
        away_advantages.append("le milieu possède un avantage qualitatif dans la zone centrale")
    if away_attack > home_defense + 0.2:
        away_advantages.append("l’attaque peut cibler une ligne défensive moins bien notée")
    elif home_defense > away_attack + 0.2:
        home_advantages.append("la ligne défensive affiche un avantage sur l’attaque adverse")

    formation_reliability = min(
        1.0 if home.get("official") else float(home.get("projection_confidence") or 0.35),
        1.0 if away.get("official") else float(away.get("projection_confidence") or 0.35),
    )
    form_reliability = min(
        float(home.get("form_reliability") or 0),
        float(away.get("form_reliability") or 0),
    )
    reliability = 0.5 * formation_reliability + 0.5 * form_reliability
    return {
        "home_formation": home_formation or "Indisponible",
        "away_formation": away_formation or "Indisponible",
        "home_profile": home_profile,
        "away_profile": away_profile,
        "structural_reading": structural_reading,
        "structural_edge": round(structural_edge, 2),
        "line_edge": round(line_edge, 2),
        "metric_edge": round(metric_edge, 2),
        "edge": round(edge, 2),
        "reliability": round(max(0.0, min(1.0, reliability)), 3),
        "home_advantages": home_advantages,
        "away_advantages": away_advantages,
        "line_matchups": {
            "home_attack_vs_away_defense": round(home_attack - away_defense, 2),
            "home_midfield_vs_away_midfield": round(home_midfield - away_midfield, 2),
            "away_attack_vs_home_defense": round(away_attack - home_defense, 2),
        },
    }


def get_match_intelligence(
    fixture_id: int,
    home_team_id: int,
    away_team_id: int,
    season: int,
    match_date,
) -> dict:
    _ensure_schema()
    league_id = _fixture_league_id(int(fixture_id))
    teams = {}
    for side, team_id in (("home", home_team_id), ("away", away_team_id)):
        lineup = _official_lineup(fixture_id, team_id)
        if lineup is None:
            lineup = _projected_lineup(
                team_id, league_id, season, before_date=match_date
            )
            if lineup is None:
                lineup = _load_projected_lineup(
                    f"fixture:{int(fixture_id)}:team:{int(team_id)}"
                )
        teams[side] = _enrich_lineup(lineup, league_id, season, match_date)
        if teams[side] and not teams[side].get("official"):
            _persist_projected_lineup(
                f"fixture:{int(fixture_id)}:team:{int(team_id)}",
                teams[side],
                league_id,
                season,
                fixture_id=int(fixture_id),
            )

    available = [team for team in teams.values() if team is not None]
    reliability = (
        min(team["intelligence_reliability"] for team in available)
        if len(available) == 2
        else 0.0
    )
    tactical = analyze_tactical_matchup(teams.get("home"), teams.get("away"))
    return {
        **teams,
        "reliability": round(reliability, 3),
        "complete": len(available) == 2,
        "tactical": tactical,
        "fixture_id": int(fixture_id),
    }


def get_projected_match_intelligence(
    home_team_id: int,
    away_team_id: int,
    league_id: int,
    season: int,
    before_date=None,
) -> dict:
    """Construit deux onze probables lorsqu’aucun match précis n’est fourni."""
    _ensure_schema()
    before_date = before_date or _now()
    teams = {
        "home": get_projected_team_intelligence(
            home_team_id, league_id, season, before_date
        ),
        "away": get_projected_team_intelligence(
            away_team_id, league_id, season, before_date
        ),
    }
    return combine_team_intelligence(teams["home"], teams["away"])


def get_projected_team_intelligence(
    team_id: int,
    league_id: int,
    season: int,
    before_date=None,
) -> dict | None:
    _ensure_schema()
    before_date = before_date or _now()
    lineup = _projected_lineup(
        int(team_id), int(league_id), int(season), before_date=before_date
    )
    scope_key = f"team:{int(team_id)}:league:{int(league_id)}:season:{int(season)}"
    if lineup is None:
        lineup = _load_projected_lineup(scope_key)
    enriched = _enrich_lineup(
        lineup,
        int(league_id),
        int(season),
        before_date,
    )
    if enriched:
        _persist_projected_lineup(
            scope_key,
            enriched,
            int(league_id),
            int(season),
        )
    return enriched


def combine_team_intelligence(home: dict | None, away: dict | None) -> dict:
    teams = {"home": home, "away": away}
    available = [team for team in teams.values() if team is not None]
    reliability = (
        min(team["intelligence_reliability"] for team in available)
        if len(available) == 2
        else 0.0
    )
    return {
        **teams,
        "reliability": round(reliability, 3),
        "complete": len(available) == 2,
        "tactical": analyze_tactical_matchup(teams.get("home"), teams.get("away")),
        "fixture_id": None,
    }


def get_prediction_intelligence(
    home_team_id: int,
    away_team_id: int,
    league_id: int,
    season: int,
    before_date=None,
) -> dict:
    """Privilégie le prochain match réel, sinon utilise deux onze projetés."""
    _ensure_schema()
    try:
        fixture = pd.read_sql(
            text(
                """
                SELECT fixture_id, date
                FROM matches
                WHERE home_team_id = :home_team
                  AND away_team_id = :away_team
                  AND league_id = :league_id
                  AND season = :season
                  AND date >= CURRENT_TIMESTAMP
                ORDER BY date ASC
                LIMIT 1
                """
            ),
            engine,
            params={
                "home_team": int(home_team_id),
                "away_team": int(away_team_id),
                "league_id": int(league_id),
                "season": int(season),
            },
        )
    except Exception:
        fixture = pd.DataFrame()
    if not fixture.empty:
        row = fixture.iloc[0]
        return get_match_intelligence(
            fixture_id=int(row["fixture_id"]),
            home_team_id=int(home_team_id),
            away_team_id=int(away_team_id),
            season=int(season),
            match_date=row["date"],
        )
    return get_projected_match_intelligence(
        home_team_id=int(home_team_id),
        away_team_id=int(away_team_id),
        league_id=int(league_id),
        season=int(season),
        before_date=before_date,
    )


def player_goal_factors(intelligence: dict | None) -> tuple[float, float]:
    """Facteurs xG prudents issus de la forme des deux compositions."""
    if not intelligence or not intelligence.get("complete"):
        return 1.0, 1.0
    reliability = max(
        0.0, min(1.0, float(intelligence.get("reliability") or 0))
    )

    def factor(team):
        score = float((team or {}).get("form_score") or 50)
        return max(
            0.92,
            min(1.08, 1 + ((score - 50) / 50) * 0.08 * reliability),
        )

    return factor(intelligence.get("home")), factor(intelligence.get("away"))


def resolve_player_season(
    team_ids: list[int],
    league_id: int,
    candidate_seasons,
) -> int:
    """Choisit la saison sélectionnée couvrant le plus d’équipes en joueurs."""
    seasons = sorted({int(value) for value in candidate_seasons}, reverse=True)
    if not seasons:
        raise ValueError("Aucune saison candidate.")
    if not team_ids:
        return seasons[0]
    season_placeholders = ",".join(
        f":season_{index}" for index in range(len(seasons))
    )
    team_placeholders = ",".join(
        f":team_{index}" for index in range(len(team_ids))
    )
    params = {"league_id": int(league_id)}
    params.update(
        {f"season_{index}": season for index, season in enumerate(seasons)}
    )
    params.update(
        {f"team_{index}": int(team_id) for index, team_id in enumerate(team_ids)}
    )
    try:
        rows = pd.read_sql(
            text(
                f"""
                SELECT season, COUNT(DISTINCT team_id) AS covered_teams
                FROM player_statistics
                WHERE league_id = :league_id
                  AND season IN ({season_placeholders})
                  AND team_id IN ({team_placeholders})
                GROUP BY season
                ORDER BY covered_teams DESC, season DESC
                LIMIT 1
                """
            ),
            engine,
            params=params,
        )
        if not rows.empty:
            return int(rows.iloc[0]["season"])
    except Exception:
        pass
    return seasons[0]
