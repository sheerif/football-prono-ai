import pandas as pd

def compute_basic_stats(matches_df, team_id):
    team_matches = matches_df[(matches_df['home_team_id']==team_id) | (matches_df['away_team_id']==team_id)]
    wins = 0
    draws = 0
    losses = 0
    goals_for = 0
    goals_against = 0
    played = 0
    for _, row in team_matches.iterrows():
        if row['home_team_id']==team_id:
            gf = row['home_goals']
            ga = row['away_goals']
        else:
            gf = row['away_goals']
            ga = row['home_goals']
        if pd.isna(gf) or pd.isna(ga):
            continue
        gf = int(gf)
        ga = int(ga)
        played += 1
        goals_for += gf
        goals_against += ga
        if gf>ga:
            wins+=1
        elif gf==ga:
            draws+=1
        else:
            losses+=1
    return {
        'played': played,
        'wins': wins,
        'draws': draws,
        'losses': losses,
        'goals_for': goals_for,
        'goals_against': goals_against
    }
