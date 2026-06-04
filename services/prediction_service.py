import numpy as np

# Placeholder simple engine combining features with weights described in spec
WEIGHTS = {
    'recent_form': 0.40,
    'ranking': 0.20,
    'home_away': 0.15,
    'h2h': 0.15,
    'off_def': 0.10,
}

def normalize_probs(arr):
    arr = np.array(arr, dtype=float)
    s = arr.sum()
    if s==0:
        return [33.33,33.33,33.33]
    return list((arr/s*100).round(2))


def predict_simple(home_strength, away_strength, draw_factor=0.2):
    """home_strength/away_strength scalars combine features into probabilities"""
    home = home_strength
    away = away_strength
    draw = draw_factor*min(home, away)
    probs = normalize_probs([home, draw, away])
    confidence = max(probs)
    return {
        'home_probability': probs[0],
        'draw_probability': probs[1],
        'away_probability': probs[2],
        'confidence': float(confidence)
    }
