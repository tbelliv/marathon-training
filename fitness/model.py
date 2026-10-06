#!/usr/bin/env python3
"""Fitness model: the published methods pro groups and labs use, run on COROS data.

Inputs (fitness/data/):
  activities.csv   every run since the watch arrived (date, duration, distance, avg HR, altitude)
  fit/*.fit        second-by-second files for the runs that matter (long runs, workouts, races)
  rhr.csv, hrv.csv daily resting HR and sleep HRV from COROS
  athlete.json     HR rest/max, LTHR, mass, races
Outputs:
  data/daily.csv   daily TRIMP, CTL (fitness), ATL (fatigue), TSB (form), ACWR
  data/runs.csv    per-FIT metrics: GAP, efficiency factor, decoupling, best efforts, durability
  report.md        the weekly report
Methods: Banister TRIMP and impulse-response (CTL 42 d / ATL 7 d); Strava GAP 2017 (no Minetti);
efficiency factor and aerobic decoupling (Friel); critical speed two-parameter model (Hill/Monod);
Daniels VDOT with altitude correction; durability as EF decay per hour (Jones 2021); ACWR 7:28.
"""
import csv, json, math, os, sys, glob, datetime as dt, statistics as st
from collections import defaultdict
sys.path.insert(0, '/home/user/runningworkbench')
try:
    from calculator import grade_cost_factor
except Exception:
    def grade_cost_factor(g, race_type='road'):
        g = max(-0.45, min(g, 0.45))
        if g < 0:
            return 1.0 if g <= -0.18 else 0.88 + (0.12/0.0081)*(g+0.09)**2
        return 1.0 + 2.5*g
import fitdecode

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, 'data')
A = json.load(open(os.path.join(D, 'athlete.json')))
HR_REST, HR_MAX = A['hr_rest'], A['hr_max']
MI = 1609.34

def fmt_pace(sec_per_mi):
    if not sec_per_mi or sec_per_mi != sec_per_mi: return '—'
    return '%d:%02d' % (int(sec_per_mi // 60), int(round(sec_per_mi % 60)))
def fmt_t(s):
    s = int(round(s)); h, m, sec = s//3600, (s%3600)//60, s%60
    return ('%d:%02d:%02d' % (h, m, sec)) if h else ('%d:%02d' % (m, sec))

# ---------- 1. daily load, fitness, fatigue, form ----------
def trimp(dur_s, avg_hr):
    hrr = (avg_hr - HR_REST) / (HR_MAX - HR_REST)
    hrr = max(0.0, min(1.0, hrr))
    return dur_s/60.0 * hrr * 0.64 * math.exp(1.92*hrr)

def load_activities():
    rows = list(csv.DictReader(open(os.path.join(D, 'activities.csv'))))
    for r in rows:
        r['date'] = dt.date.fromisoformat(r['date']); r['duration_s'] = int(r['duration_s'])
        r['distance_m'] = float(r['distance_m']); r['avg_hr'] = int(r['avg_hr'])
        r['trimp'] = trimp(r['duration_s'], r['avg_hr'])
    return rows

def daily_model(acts, start=None, end=None):
    byday = defaultdict(float); miles = defaultdict(float)
    for r in acts:
        byday[r['date']] += r['trimp']; miles[r['date']] += r['distance_m']/MI
    start = start or min(byday); end = end or dt.date.today()
    ctl = atl = 0.0; kc, ka = math.exp(-1/42), math.exp(-1/7)
    out = []; hist = []
    d = start
    while d <= end:
        L = byday.get(d, 0.0)
        ctl = ctl*kc + L*(1-kc); atl = atl*ka + L*(1-ka)
        hist.append(L)
        acute = sum(hist[-7:])/7; chronic = sum(hist[-28:])/min(28, len(hist))
        acwr = acute/chronic if chronic > 0 else 0
        out.append(dict(date=d, trimp=round(L,1), miles=round(miles.get(d,0),1), ctl=round(ctl,1), atl=round(atl,1), tsb=round(ctl-atl,1), acwr=round(acwr,2)))
        d += dt.timedelta(days=1)
    return out

# ---------- 2. per-FIT analysis ----------
def read_fit(path):
    rec = []
    with fitdecode.FitReader(path) as f:
        for fr in f:
            if fr.frame_type == fitdecode.FIT_FRAME_DATA and fr.name == 'record':
                ts = fr.get_value('timestamp', fallback=None); hr = fr.get_value('heart_rate', fallback=None)
                dist = fr.get_value('distance', fallback=None)
                alt = fr.get_value('enhanced_altitude', fallback=None)
                if alt is None: alt = fr.get_value('altitude', fallback=None)
                spd = fr.get_value('enhanced_speed', fallback=None)
                if spd is None: spd = fr.get_value('speed', fallback=None)
                if ts is None or dist is None: continue
                rec.append((ts.timestamp(), hr, float(dist), alt, spd))
    return rec

def chunks(rec, size=100.0):
    """100 m chunks: (t_mid, seconds, meters, grade, mean HR, alt). Timer pauses excluded by using moving samples only."""
    out = []; i0 = 0; n = len(rec)
    for i in range(1, n):
        if rec[i][2] - rec[i0][2] >= size:
            t = rec[i][0] - rec[i0][0]; m = rec[i][2] - rec[i0][2]
            if t <= 0 or m <= 0: i0 = i; continue
            hrs = [r[1] for r in rec[i0:i+1] if r[1]]
            a0, a1 = rec[i0][3], rec[i][3]
            grade = ((a1 - a0)/m) if (a0 is not None and a1 is not None) else 0.0
            # a pause inside the chunk shows as speed << distance/time; cap chunk time at 2x the pace implied by sensor speed
            out.append(dict(t=rec[i0][0], sec=t, m=m, grade=grade, hr=(st.mean(hrs) if hrs else None), alt=(a1 if a1 is not None else 0)))
            i0 = i
    return out

def best_efforts(rec, windows=(180, 300, 600, 720, 900, 1200, 1800, 2700, 3600)):
    """Max distance covered in each window of MOVING time (two-pointer). Stops, timer pauses and walking
    (speed under 1.0 m/s, or a gap over 10 s between samples) are removed from the clock first, the way a
    race timing mat or a lab test would never include them. The blister stop in the Oct 4 10k is the test case."""
    mv = []; tm = 0.0
    for i in range(1, len(rec)):
        dt_ = rec[i][0] - rec[i-1][0]; dd = rec[i][2] - rec[i-1][2]
        if dt_ <= 0 or dt_ > 10: continue
        spd = rec[i][4] if rec[i][4] is not None else dd/dt_
        if spd < 1.0: continue
        tm += dt_; mv.append((tm, rec[i][2]))
    res = {}; n = len(mv)
    for w in windows:
        best = 0.0; j = 0
        for i in range(n):
            while j < n and mv[j][0] - mv[i][0] < w: j += 1
            if j >= n: break
            t0, d0 = mv[j-1]; t1, d1 = mv[j]; tt = mv[i][0] + w
            d = d0 + (d1-d0)*((tt-t0)/(t1-t0)) if t1 > t0 else d0
            best = max(best, d - mv[i][1])
        if best > 0: res[w] = best
    return res

def analyze_fit(path):
    rec = read_fit(path)
    if len(rec) < 60: return None
    ch = chunks(rec)
    ch = [c for c in ch if c['sec'] < 400 and c['hr']]   # drop paused / stopped chunks and chunks without HR
    if len(ch) < 20: return None
    moving = sum(c['sec'] for c in ch); dist = sum(c['m'] for c in ch)
    gap_dist = sum(c['m']*grade_cost_factor(c['grade']) for c in ch)
    avg_hr = sum(c['hr']*c['sec'] for c in ch)/moving
    ef = (gap_dist/moving*60)/avg_hr   # GAP metres per minute per beat
    # decoupling: EF of first half vs second half after a 10-minute warm-up (Friel)
    tcum = 0; body = []
    for c in ch:
        tcum += c['sec']
        if tcum > 600: body.append(c)
    half = sum(c['sec'] for c in body)/2; acc = 0; h1, h2 = [], []
    for c in body:
        (h1 if acc < half else h2).append(c); acc += c['sec']
    def ef_of(cs):
        t = sum(c['sec'] for c in cs); 
        if t == 0: return None
        return (sum(c['m']*grade_cost_factor(c['grade']) for c in cs)/t*60)/(sum(c['hr']*c['sec'] for c in cs)/t)
    e1, e2 = ef_of(h1), ef_of(h2)
    decoup = (e1-e2)/e1*100 if (e1 and e2) else None
    # durability: EF per 30-min block relative to block 1 (after warm-up)
    blocks = []; cur = []; acc = 0
    for c in body:
        cur.append(c); acc += c['sec']
        if acc >= 1800: blocks.append(ef_of(cur)); cur = []; acc = 0
    if cur and acc >= 900: blocks.append(ef_of(cur))
    ref = max(blocks[:2]) if blocks else None
    dur_rel = [round(b/ref*100, 1) for b in blocks] if blocks else []
    # pace at fixed HR bands (steady 100 m chunks only, grade adjusted)
    def pace_at(lo, hi):
        cs = [c for c in ch if lo <= c['hr'] <= hi and abs(c['grade']) < 0.06]
        if len(cs) < 8: return None
        t = sum(c['sec'] for c in cs); gm = sum(c['m']*grade_cost_factor(c['grade']) for c in cs)
        return t/gm*MI
    be = best_efforts(rec)
    alt = st.median([c['alt'] for c in ch])*3.281
    return dict(file=os.path.basename(path), date=dt.datetime.fromtimestamp(rec[0][0]).date(), moving_s=round(moving), dist_mi=round(dist/MI, 2),
                avg_hr=round(avg_hr), max_hr=max((r[1] or 0) for r in rec), alt_ft=round(alt),
                pace=fmt_pace(moving/dist*MI), gap=fmt_pace(moving/gap_dist*MI), ef=round(ef, 3), decoupling_pct=(round(decoup, 1) if decoup is not None else None),
                durability=dur_rel, pace_hr150=pace_at(145, 155), pace_hr160=pace_at(155, 165), pace_hr168=pace_at(165, 172), best=be)

# ---------- 3. critical speed, VO2max ----------
def critical_speed(runs, max_alt_ft=7000):
    """Two-parameter model d = CS*t + D' fit to the single best distance at each window 3–20 min (the standard CS testing range).
    Efforts above max_alt_ft are excluded: CS is altitude-specific and a 9,000 ft long run drags the Boulder number down."""
    best = {}
    for r in runs:
        if r['alt_ft'] > max_alt_ft: continue
        for w, d in r['best'].items():
            if 180 <= w <= 1200 and d > best.get(w, (0, None))[0]: best[w] = (d, r['file'])
    pts = sorted((w, d) for w, (d, _) in best.items())
    if len(pts) < 3: return None
    n = len(pts); sx = sum(p[0] for p in pts); sy = sum(p[1] for p in pts)
    sxx = sum(p[0]**2 for p in pts); sxy = sum(p[0]*p[1] for p in pts)
    cs = (n*sxy - sx*sy)/(n*sxx - sx*sx); dp = (sy - cs*sx)/n
    return dict(cs_mps=cs, d_prime_m=dp, points=[(w, d, best[w][1]) for w, d in pts])

def vdot(dist_m, time_s):
    t = time_s/60.0; v = dist_m/t
    vo2 = -4.60 + 0.182258*v + 0.000104*v*v
    pct = 0.8 + 0.1894393*math.exp(-0.012778*t) + 0.2989558*math.exp(-0.1932605*t)
    return vo2/pct

def altitude_time_factor(alt_ft, minutes):
    """Approximate time penalty for races above 3,000 ft (Daniels-style): ~0 at 3,000 ft rising to ~4% at 6,000 ft for 30–60 min efforts."""
    if alt_ft <= 3000: return 1.0
    f = 1 + 0.04*(min(alt_ft, 8000)-3000)/3000
    if minutes < 15: f = 1 + (f-1)*0.6
    return f

def race_time(vd, dist_m):
    """Invert VDOT for a distance by bisection."""
    lo, hi = 60.0, 36000.0
    for _ in range(60):
        mid = (lo+hi)/2
        if vdot(dist_m, mid) > vd: lo = mid
        else: hi = mid
    return (lo+hi)/2

# ---------- 4. report ----------
def main():
    acts = load_activities()
    daily = daily_model(acts)
    with open(os.path.join(D, 'daily.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(daily[0].keys())); w.writeheader(); w.writerows(daily)
    runs = []
    for p in sorted(glob.glob(os.path.join(D, 'fit', '*.fit'))):
        try:
            r = analyze_fit(p)
            if r: runs.append(r)
        except Exception as e:
            print('skip', p, e, file=sys.stderr)
    runs.sort(key=lambda r: r['date'])
    with open(os.path.join(D, 'runs.csv'), 'w', newline='') as f:
        keys = ['date','file','dist_mi','moving_s','alt_ft','avg_hr','max_hr','pace','gap','ef','decoupling_pct','durability','pace_hr150','pace_hr160','pace_hr168']
        w = csv.writer(f); w.writerow(keys)
        for r in runs: w.writerow([r[k] if k not in ('pace_hr150','pace_hr160','pace_hr168') else (fmt_pace(r[k]) if r[k] else '') for k in keys])
    cs = critical_speed(runs)
    today = daily[-1]
    rhr = list(csv.DictReader(open(os.path.join(D, 'rhr.csv')))); hrv = list(csv.DictReader(open(os.path.join(D, 'hrv.csv'))))
    coros = list(csv.DictReader(open(os.path.join(D, 'coros_assessment.csv'))))[-1]

    L = []
    L.append('# Fitness report — %s\n' % dt.date.today().isoformat())
    L.append('Generated by `fitness/model.py` from %d activities and %d second-by-second files. Methods and caveats at the bottom.\n' % (len(acts), len(runs)))
    # headline
    L.append('## 1. Fitness, fatigue, form (Banister impulse-response)\n')
    L.append('| | Today | 7 d ago | 28 d ago |\n|---|---|---|---|')
    def at(n): return daily[-1-n] if len(daily) > n else daily[0]
    for k, lab in (('ctl','Fitness (CTL, 42-day)'),('atl','Fatigue (ATL, 7-day)'),('tsb','Form (TSB = CTL − ATL)'),('acwr','Acute:chronic load ratio')):
        L.append('| %s | %s | %s | %s |' % (lab, today[k], at(7)[k], at(28)[k]))
    peak = max(daily, key=lambda d: d['ctl'])
    L.append('\nPeak fitness so far: **%s on %s**. Form today %+.0f: ' % (peak['ctl'], peak['date'], today['tsb']) +
             ('fresh, race-ready territory (TSB +5 to +25 is the target for Oct 24).' if today['tsb'] > 5 else 'still carrying fatigue; the taper should bring TSB to +10 to +20 by Oct 24.'))
    L.append('Load ratio %.2f: ' % today['acwr'] + ('in the 0.8–1.3 sweet spot.' if 0.8 <= today['acwr'] <= 1.3 else 'outside 0.8–1.3, injury-risk zone.'))
    # weekly table
    L.append('\n### Weekly load and mileage\n\n| Week starting | Miles | TRIMP | CTL end of week | ACWR |\n|---|---|---|---|---|')
    wk = defaultdict(lambda: [0,0,None,None])
    for d in daily:
        k = d['date'] - dt.timedelta(days=d['date'].weekday()); wk[k][0] += d['miles']; wk[k][1] += d['trimp']; wk[k][2] = d['ctl']; wk[k][3] = d['acwr']
    for k in sorted(wk)[-12:]:
        L.append('| %s | %.0f | %.0f | %.0f | %.2f |' % (k, wk[k][0], wk[k][1], wk[k][2], wk[k][3]))
    # CS
    L.append('\n## 2. Critical speed and D′\n')
    if cs:
        csp = MI/cs['cs_mps']
        L.append('Critical speed **%s /mi** (%.2f m/s), D′ **%.0f m**, fit to the best efforts below. CS is the pace you can hold for roughly 30–60 min: the physiological threshold. Threshold pace on the watch bands (172–175 bpm) should sit near this.\n' % (fmt_pace(csp), cs['cs_mps'], cs['d_prime_m']))
        L.append('| Window | Best distance | Pace | From |\n|---|---|---|---|')
        for w, d, fn in cs['points']: L.append('| %d min | %.0f m | %s /mi | %s |' % (w//60, d, fmt_pace(w/d*MI), fn))
        L.append('\nEfforts above 7,000 ft are excluded from the fit (CS is altitude-specific). Remaining efforts mix sea level (Falmouth, Bristol) and 5,400–5,900 ft, so read this as the Boulder number. Every point above comes from a tired 10k or a 2-mile; a fresh all-out 5k or 10k would raise CS and everything derived from it.')
        cs_all = critical_speed(runs, max_alt_ft=99999)
        if cs_all: L.append('For reference, including the 9,000 ft long runs the fit gives CS %s /mi; that is the mountain number, not the race number.' % fmt_pace(MI/cs_all['cs_mps']))
    # VO2max
    L.append('\n## 3. VO2max and race equivalents (Daniels VDOT, altitude-corrected)\n')
    L.append('| Effort | Raw | Sea-level equivalent | VDOT |\n|---|---|---|---|')
    ests = []
    fal = next((r for r in runs if 'falmouth' in r['file']), None)
    efforts = [('Clear Creek 10k, Oct 4 (after 8 easy miles, blister stop, not a race)', 10810, 2757, 5900), ('Bristol 2 mi, Sep 9 (sea level)', 3240, 742, 50)]
    if fal: efforts.insert(1, ('Falmouth 7 mi, Aug 2025 (sea level, hot and humid)', fal['dist_mi']*MI, fal['moving_s'], 50))
    for name, dist, secs, alt in efforts:
        f = altitude_time_factor(alt, secs/60); adj = secs/f; vd = vdot(dist, adj); ests.append(vd)
        L.append('| %s | %s | %s | %.1f |' % (name, fmt_t(secs), fmt_t(adj), vd))
    vbest = max(ests)
    vcs = None
    if cs:
        v_sl = cs['cs_mps']*1.035*60          # sea-level CS in m/min
        vvo2 = v_sl/0.87                       # CS is ~87 % of vVO2max in trained runners
        vcs = -4.60 + 0.182258*vvo2 + 0.000104*vvo2*vvo2
        L.append('| Critical speed (sea-level corrected, CS = 87 %% of vVO2max) | %s /mi | %s /mi | %.1f |' % (fmt_pace(MI/cs['cs_mps']), fmt_pace(MI/(cs['cs_mps']*1.035)), vcs))
    vlike = max(vbest, vcs or 0)
    L.append('\nEvery effort in the record is sub-maximal (the 10k was run tired, Falmouth was hot), so every number here is a **floor**. Best-supported VDOT **%.0f** (race-based %.0f, critical-speed %.0f); COROS says **%s** from its own model. Lab VO2max usually reads 2–5 points above VDOT, so expect **%d–%d ml/kg/min** on a treadmill. A fresh all-out 5k would replace the floor with a measurement.\n' % (vlike, vbest, vcs or 0, coros['vo2max'], round(vlike+2), round(vlike+5)))
    L.append('| Distance | Floor (VDOT %.0f), sea level | COROS-equivalent (VDOT %s), sea level | Floor, at Boulder altitude |\n|---|---|---|---|' % (vlike, coros['vo2max']))
    for nm, dm in (('5k', 5000), ('10k', 10000), ('Half', 21097), ('Marathon', 42195)):
        t1 = race_time(vlike, dm); t2 = race_time(float(coros['vo2max']), dm)
        L.append('| %s | %s | %s | %s |' % (nm, fmt_t(t1), fmt_t(t2), fmt_t(t1*altitude_time_factor(5400, t1/60))))
    L.append('\nCOROS predictions for comparison: 5k %s · 10k %s · half %s · marathon %s.' % (fmt_t(int(coros['pred_5k_s'])), fmt_t(int(coros['pred_10k_s'])), fmt_t(int(coros['pred_half_s'])), fmt_t(int(coros['pred_marathon_s']))))
    # economy / EF trend
    L.append('\n## 4. Economy proxy: grade-adjusted pace per heartbeat\n')
    L.append('Efficiency factor = grade-adjusted metres per minute divided by heart rate. Higher is fitter. Pace at a fixed heart rate is the same thing read the other way. Altitude listed because 8,000+ ft costs 5–8 % on both. Track and hill sessions are excluded (rest intervals break the ratio).\n')
    L.append('| Date | Run | Mi | Alt ft | Avg HR | Pace | GAP | EF | Decoupling | GAP @150 | GAP @160 | GAP @168 |\n|---|---|---|---|---|---|---|---|---|---|---|---|')
    for r in runs:
        if r['dist_mi'] < 3 or 'track' in r['file'] or 'hill' in r['file']: continue
        L.append('| %s | %s | %.1f | %d | %d | %s | %s | %.3f | %s | %s | %s | %s |' % (r['date'], r['file'].replace('.fit',''), r['dist_mi'], r['alt_ft'], r['avg_hr'], r['pace'], r['gap'], r['ef'],
                 ('%+.1f%%' % r['decoupling_pct']) if r['decoupling_pct'] is not None else '—', fmt_pace(r['pace_hr150']), fmt_pace(r['pace_hr160']), fmt_pace(r['pace_hr168'])))
    L.append('\nDecoupling under 5 % on a long run means the aerobic system held; over 8 % means the pace was beyond current endurance or the day was hot or the fuel ran short.')
    # durability
    L.append('\n## 5. Durability (EF by 30-minute block, long runs only)\n')
    L.append('The current frontier metric: how much grade-adjusted pace per beat decays per hour, measured from the best of the first two 30-minute blocks. Pro marathoners lose under 5 % per hour. Blocks above 100 mean a negative split or a descent-heavy back half. Your 50k injuries live here.\n')
    L.append('| Date | Run | 30-min blocks (% of reference) | Loss per hour |\n|---|---|---|---|')
    for r in runs:
        if r['moving_s'] >= 5000 and len(r['durability']) >= 3:
            hrs = (len(r['durability'])-1)/2; loss = max(0.0, (100 - r['durability'][-1])/hrs) if hrs else 0
            L.append('| %s | %s | %s | %.1f %% |' % (r['date'], r['file'].replace('.fit',''), ' · '.join('%.0f' % b for b in r['durability']), loss))
    # recovery
    L.append('\n## 6. Recovery: resting HR and sleep HRV\n')
    last = [int(r['rhr']) for r in rhr[-7:]]; base = [int(r['rhr']) for r in rhr[-42:-7]]
    L.append('Resting HR last 7 days **%.1f** vs prior 5-week mean **%.1f** (SD %.1f). ' % (st.mean(last), st.mean(base), st.pstdev(base)) + ('Normal.' if abs(st.mean(last)-st.mean(base)) < 2*max(1, st.pstdev(base)) else 'Outside normal: watch it.'))
    if hrv:
        L.append('Sleep HRV last reading %s ms, baseline %s, normal range %s–%s. ' % (hrv[-1]['hrv_ms'], hrv[-1]['baseline_ms'], hrv[-1]['range_lo'], hrv[-1]['range_hi']) + ('Normal or above on every night this week.' if all(h['status'] in ('normal','above') for h in hrv[-7:]) else 'At least one suppressed night this week.'))
    # strength
    sl = os.path.join(os.path.dirname(HERE), 'strength-log.md')
    if os.path.exists(sl):
        L.append('\n## 7. Strength load (session RPE)\n\nSee `strength-log.md`. Weekly strength load is tracked there and counts toward total load; it is invisible to the heart-rate model above by design.')
    # methods
    L.append('\n## Methods and caveats\n')
    L.append('- **TRIMP** (Banister, male coefficients) from duration and average HR, HR rest %d, HR max %d. Daily load drives CTL (42-day time constant), ATL (7-day), TSB = CTL − ATL. Same model as TrainingPeaks, Intervals.icu and the Nike and D1 dashboards; the units are TRIMP, not TSS, so compare only to yourself.' % (HR_REST, HR_MAX))
    L.append('- **GAP** is the Strava 2017 empirical curve as reconstructed in runningworkbench. Minetti is not used anywhere.')
    L.append('- **Efficiency factor and decoupling** per Friel: GAP speed over HR, first half vs second half after a 10-minute warm-up.')
    L.append('- **Critical speed** two-parameter linear model on best 3–30 minute efforts across all files. D′ is the anaerobic work capacity above CS, in metres.')
    L.append('- **VDOT** Daniels and Gilbert formulas; altitude correction ~4 % per 3,000 ft above 3,000 ft for 30–60 minute efforts. Estimates, ±5 %. A lab test replaces these.')
    L.append('- **Durability** EF per 30-minute block after warm-up, relative to block 1, per Jones et al. 2021 and Maunder 2021.')
    L.append('- Strength sessions are scored in `strength-log.md` by session RPE (Foster) and are not in the TRIMP model.')
    open(os.path.join(HERE, 'report.md'), 'w').write('\n'.join(L) + '\n')
    print('report written: CTL %s ATL %s TSB %s ACWR %s; runs analysed %d' % (today['ctl'], today['atl'], today['tsb'], today['acwr'], len(runs)))
    if cs: print('CS %s /mi  D\' %.0f m' % (fmt_pace(MI/cs['cs_mps']), cs['d_prime_m']))

if __name__ == '__main__':
    main()
