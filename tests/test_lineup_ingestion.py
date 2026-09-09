import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from database import models
from services import lineup_service


class FixturePlayerIngestionTests(unittest.TestCase):
    def test_duplicate_api_players_are_upserted_once(self):
        engine = create_engine("sqlite://")
        models.Base.metadata.create_all(engine)
        session_factory = sessionmaker(bind=engine)
        duplicate = {
            "team": {"id": 81},
            "players": [
                {
                    "player": {"id": 36827, "name": "J. de Lange"},
                    "statistics": [{"games": {"minutes": 90, "position": "G"}}],
                },
                {
                    "player": {"id": 36827, "name": "J. de Lange"},
                    "statistics": [{"games": {"minutes": 90, "position": "G"}}],
                },
            ],
        }

        with session_factory() as session:
            saved = lineup_service._save_fixture_players(session, 1552751, [duplicate])
            session.commit()
            rows = session.scalars(select(models.FixturePlayerStatistic)).all()

        self.assertEqual(saved, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].player_id, 36827)


if __name__ == "__main__":
    unittest.main()
