"""Evaluation seeds, fixed BEFORE any tuning (spec §16): disjoint roles, never reused across roles."""

TUNING = tuple(range(1, 21))          # thresholds (z1, z2, beta, ...) are tuned on these only
EVAL = tuple(range(101, 121))         # PRIMARY_EVAL: every headline number
EVAL_REDUCED = tuple(range(101, 111))  # the fixed subset if the measured cost exceeds the time available
WARMUP = (901, 902, 903)              # simulation autonomy qualification (Stage 3)
RELIABILITY = 904                     # held-out reliability world (Stage 3)
STRESS = ("no_cannibalization", "double_weekly_seasonality", "tracking_lag_2d")  # STRESS_EVAL, never pooled

assert not set(TUNING) & set(EVAL) and not set(EVAL) & set(WARMUP) and RELIABILITY not in WARMUP
