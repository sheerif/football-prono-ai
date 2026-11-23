"""Arrow-safe dataframe construction for mixed display-only values."""

import pandas as pd


def finished_jobs_dataframe(rows: list[dict]) -> pd.DataFrame:
    """Build the completed-jobs table with stable string types for display columns.

    API call and avoided-request values are numeric when available but use an
    em dash when unavailable. Explicit string dtype prevents Arrow from
    inferring int64 from the numeric rows and then failing on the dash.
    """
    frame = pd.DataFrame(rows)
    for column in ("Appels API", "Requêtes évitées"):
        if column in frame.columns:
            frame[column] = frame[column].astype("string")
    return frame
