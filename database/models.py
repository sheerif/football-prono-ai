from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float, Boolean
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
