import gzip
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SEED_DIRECTORY = ROOT / "database" / "seed"
EXPECTED_DATABASE_SHA256 = (
    "d7930409149acf6c211702cab896144fe2aa715380c711e3db06d87fc8c948b8"
)


class DatabaseSeedTests(unittest.TestCase):
    def test_seed_archive_is_complete_and_matches_runtime_checksum(self):
        digest = hashlib.sha256()
        with gzip.open(SEED_DIRECTORY / "football-cache-v3.db.gz", "rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)

        self.assertEqual(digest.hexdigest(), EXPECTED_DATABASE_SHA256)

    def test_seed_metadata_contains_no_authentication_token(self):
        metadata = json.loads(
            (SEED_DIRECTORY / "football-cache-v3.db-info.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertNotIn("auth_token", metadata)
        self.assertNotIn("token", metadata)
        self.assertIn("synced_revision", metadata)


if __name__ == "__main__":
    unittest.main()
