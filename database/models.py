from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float, Boolean, Text
from sqlalchemy.orm import relationship
from .database import Base
import datetime

class League(Base):
    __tablename__ = "leagues"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    country = Column(String, nullable=True)
    logo = Column(String, nullable=True)
    teams = relationship("Team", back_populates="league")

class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True, index=True)
    league_id = Column(Integer, ForeignKey("leagues.id"), nullable=True)
    name = Column(String, nullable=False)
    logo = Column(String, nullable=True)
    country = Column(String, nullable=True)
    league = relationship("League", back_populates="teams")

class Match(Base):
    __tablename__ = "matches"
    fixture_id = Column(Integer, primary_key=True, index=True)
    league_id = Column(Integer, nullable=False)
    season = Column(Integer, nullable=False)
    date = Column(DateTime, default=datetime.datetime.utcnow)
    home_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    away_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    home_goals = Column(Integer, nullable=True)
    away_goals = Column(Integer, nullable=True)
    winner = Column(String, nullable=True)
    status = Column(String, nullable=True)

class Standing(Base):
    __tablename__ = "standings"
    id = Column(Integer, primary_key=True, index=True)
    league_id = Column(Integer, nullable=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    season = Column(Integer, nullable=False)
    position = Column(Integer, nullable=True)
    points = Column(Integer, nullable=True)
    wins = Column(Integer, nullable=True)
    draws = Column(Integer, nullable=True)
    losses = Column(Integer, nullable=True)
    goals_for = Column(Integer, nullable=True)
    goals_against = Column(Integer, nullable=True)
    goal_difference = Column(Integer, nullable=True)

class Prediction(Base):
    __tablename__ = "predictions"
    fixture_id = Column(Integer, primary_key=True, index=True)
    home_probability = Column(Float, nullable=False)
    draw_probability = Column(Float, nullable=False)
    away_probability = Column(Float, nullable=False)
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Player(Base):
    __tablename__ = "players"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    firstname = Column(String, nullable=True)
    lastname = Column(String, nullable=True)
    age = Column(Integer, nullable=True)
    birth_date = Column(String, nullable=True)
    birth_place = Column(String, nullable=True)
    birth_country = Column(String, nullable=True)
    nationality = Column(String, nullable=True)
    height = Column(String, nullable=True)
    weight = Column(String, nullable=True)
    injured = Column(Boolean, nullable=True)
    photo = Column(String, nullable=True)
    raw_json = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class PlayerStatistic(Base):
    __tablename__ = "player_statistics"
    player_id = Column(Integer, ForeignKey("players.id"), primary_key=True)
    league_id = Column(Integer, primary_key=True)
    season = Column(Integer, primary_key=True)
    team_id = Column(Integer, primary_key=True)
    team_name = Column(String, nullable=True)
    team_logo = Column(String, nullable=True)
    league_name = Column(String, nullable=True)
    league_country = Column(String, nullable=True)
    league_logo = Column(String, nullable=True)
    league_flag = Column(String, nullable=True)
    games_appearences = Column(Integer, nullable=True)
    games_lineups = Column(Integer, nullable=True)
    games_minutes = Column(Integer, nullable=True)
    games_number = Column(Integer, nullable=True)
    games_position = Column(String, nullable=True)
    games_rating = Column(Float, nullable=True)
    games_captain = Column(Boolean, nullable=True)
    substitutes_in = Column(Integer, nullable=True)
    substitutes_out = Column(Integer, nullable=True)
    substitutes_bench = Column(Integer, nullable=True)
    shots_total = Column(Integer, nullable=True)
    shots_on = Column(Integer, nullable=True)
    goals_total = Column(Integer, nullable=True)
    goals_conceded = Column(Integer, nullable=True)
    goals_assists = Column(Integer, nullable=True)
    goals_saves = Column(Integer, nullable=True)
    passes_total = Column(Integer, nullable=True)
    passes_key = Column(Integer, nullable=True)
    passes_accuracy = Column(Integer, nullable=True)
    tackles_total = Column(Integer, nullable=True)
    tackles_blocks = Column(Integer, nullable=True)
    tackles_interceptions = Column(Integer, nullable=True)
    duels_total = Column(Integer, nullable=True)
    duels_won = Column(Integer, nullable=True)
    dribbles_attempts = Column(Integer, nullable=True)
    dribbles_success = Column(Integer, nullable=True)
    dribbles_past = Column(Integer, nullable=True)
    fouls_drawn = Column(Integer, nullable=True)
    fouls_committed = Column(Integer, nullable=True)
    cards_yellow = Column(Integer, nullable=True)
    cards_yellowred = Column(Integer, nullable=True)
    cards_red = Column(Integer, nullable=True)
    penalty_won = Column(Integer, nullable=True)
    penalty_committed = Column(Integer, nullable=True)
    penalty_scored = Column(Integer, nullable=True)
    penalty_missed = Column(Integer, nullable=True)
    penalty_saved = Column(Integer, nullable=True)
    raw_json = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class FixtureLineup(Base):
    __tablename__ = "fixture_lineups"
    fixture_id = Column(Integer, primary_key=True)
    team_id = Column(Integer, primary_key=True)
    team_name = Column(String, nullable=True)
    team_logo = Column(String, nullable=True)
    formation = Column(String, nullable=True)
    coach_id = Column(Integer, nullable=True)
    coach_name = Column(String, nullable=True)
    coach_photo = Column(String, nullable=True)
    raw_json = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class FixtureLineupPlayer(Base):
    __tablename__ = "fixture_lineup_players"
    fixture_id = Column(Integer, primary_key=True)
    team_id = Column(Integer, primary_key=True)
    player_id = Column(Integer, primary_key=True)
    player_name = Column(String, nullable=True)
    number = Column(Integer, nullable=True)
    position = Column(String, nullable=True)
    grid = Column(String, nullable=True)
    starter = Column(Boolean, nullable=False, default=False)
    raw_json = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class FixturePlayerStatistic(Base):
    __tablename__ = "fixture_player_statistics"
    fixture_id = Column(Integer, primary_key=True)
    team_id = Column(Integer, primary_key=True)
    player_id = Column(Integer, primary_key=True)
    player_name = Column(String, nullable=True)
    player_photo = Column(String, nullable=True)
    minutes = Column(Integer, nullable=True)
    number = Column(Integer, nullable=True)
    position = Column(String, nullable=True)
    rating = Column(Float, nullable=True)
    captain = Column(Boolean, nullable=True)
    substitute = Column(Boolean, nullable=True)
    offsides = Column(Integer, nullable=True)
    shots_total = Column(Integer, nullable=True)
    shots_on = Column(Integer, nullable=True)
    goals_total = Column(Integer, nullable=True)
    goals_conceded = Column(Integer, nullable=True)
    goals_assists = Column(Integer, nullable=True)
    goals_saves = Column(Integer, nullable=True)
    passes_total = Column(Integer, nullable=True)
    passes_key = Column(Integer, nullable=True)
    passes_accuracy = Column(Integer, nullable=True)
    tackles_total = Column(Integer, nullable=True)
    tackles_blocks = Column(Integer, nullable=True)
    tackles_interceptions = Column(Integer, nullable=True)
    duels_total = Column(Integer, nullable=True)
    duels_won = Column(Integer, nullable=True)
    dribbles_attempts = Column(Integer, nullable=True)
    dribbles_success = Column(Integer, nullable=True)
    dribbles_past = Column(Integer, nullable=True)
    fouls_drawn = Column(Integer, nullable=True)
    fouls_committed = Column(Integer, nullable=True)
    cards_yellow = Column(Integer, nullable=True)
    cards_red = Column(Integer, nullable=True)
    penalty_won = Column(Integer, nullable=True)
    penalty_committed = Column(Integer, nullable=True)
    penalty_scored = Column(Integer, nullable=True)
    penalty_missed = Column(Integer, nullable=True)
    penalty_saved = Column(Integer, nullable=True)
    raw_json = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class FixturePlayerSync(Base):
    __tablename__ = "fixture_player_sync"
    fixture_id = Column(Integer, primary_key=True)
    status = Column(String, nullable=False)
    player_count = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class ProjectedLineup(Base):
    __tablename__ = "projected_lineups"
    scope_key = Column(String, primary_key=True)
    fixture_id = Column(Integer, nullable=True, index=True)
    team_id = Column(Integer, nullable=False, index=True)
    league_id = Column(Integer, nullable=False, index=True)
    season = Column(Integer, nullable=False, index=True)
    formation = Column(String, nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    formation_source = Column(Text, nullable=True)
    player_source = Column(Text, nullable=True)
    strategy = Column(Text, nullable=True)
    lineup_json = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)


class MatchAnalysisSnapshot(Base):
    __tablename__ = "match_analysis_snapshots"
    scope_key = Column(String, primary_key=True)
    analysis_type = Column(String, nullable=False)
    fixture_id = Column(Integer, nullable=True, index=True)
    league_id = Column(Integer, nullable=True, index=True)
    season = Column(Integer, nullable=True, index=True)
    home_team_id = Column(Integer, nullable=False)
    away_team_id = Column(Integer, nullable=False)
    prediction_json = Column(Text, nullable=False)
    score_prediction_json = Column(Text, nullable=True)
    player_intelligence_json = Column(Text, nullable=True)
    tactical_analysis_json = Column(Text, nullable=True)
    model_details_json = Column(Text, nullable=True)
    cross_insight_json = Column(Text, nullable=True)
    context_json = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=False)
