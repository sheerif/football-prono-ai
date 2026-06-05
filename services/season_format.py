import pandas as pd


def season_period(value) -> str:
    if value is None or pd.isna(value):
        return ""
    season = int(value)
    return f"{season}-{season + 1}"


def season_list(values, reverse: bool = True) -> str:
    seasons = [int(value) for value in values if value is not None and not pd.isna(value)]
    if not seasons:
        return "aucune saison"
    ordered = sorted(set(seasons), reverse=reverse)
    return ", ".join(season_period(season) for season in ordered)


def season_range(values) -> str:
    seasons = sorted({int(value) for value in values if value is not None and not pd.isna(value)})
    if not seasons:
        return "Aucune"
    if len(seasons) == 1:
        return season_period(seasons[0])
    return f"{season_period(seasons[0])} à {season_period(seasons[-1])}"
