# Fitness model

The methods pro groups, D1 labs and the sports-science literature use, run weekly on Tim's COROS data. No proprietary formulas: everything here is published and is listed at the bottom of `report.md`.

- `model.py` — the model. Run `python3 fitness/model.py` from the repo root. Needs `fitdecode` and `/home/user/runningworkbench/calculator.py` for Strava GAP (falls back to the same curve inline).
- `report.md` — the current report. Regenerated every Monday by the scheduled refresh and after any manual pull.
- `data/activities.csv` — every run since the COROS watch arrived (Jul 28 2026). Source: querySportRecords.
- `data/fit/` — second-by-second files for the runs that matter: long runs, workouts, races.
- `data/rhr.csv`, `data/hrv.csv` — daily resting HR and sleep HRV (COROS daily assessment values only).
- `data/coros_assessment.csv` — COROS's own VO2max, threshold pace, predictions and load, for comparison.
- `data/daily.csv`, `data/runs.csv` — model outputs.
- `data/athlete.json` — HR rest/max, LTHR, mass, races.

What the report answers each week: is fitness rising (CTL), is fatigue in hand (ATL, TSB, ACWR), is threshold moving (critical speed), is economy improving (grade-adjusted pace per beat), is durability improving (EF decay per hour on long runs), is recovery normal (RHR, HRV), and how the race equivalents compare to COROS.

What it cannot do: lab VO2max, lactate threshold, running economy in ml/kg/km, body composition. One lab test after Oct 24 anchors every estimate here.
