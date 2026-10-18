"""Consensus race-day read from canyonlands-2026/weather/{ensemble,latest}.json. Usage: python3 fitness/weather_summary.py [YYYY-MM-DD]"""
import json, sys, math, os
D='canyonlands-2026/weather'; day=sys.argv[1] if len(sys.argv)>1 else '2026-10-24'
e=json.load(open(f'{D}/ensemble.json'))['hourly']; t=e['time']
def mem(v): return [k for k in e if k.startswith(v)]
def pct(vals,p):
    vals=sorted(x for x in vals if x is not None); i=(len(vals)-1)*p; lo=int(i); hi=min(lo+1,len(vals)-1); return vals[lo]+(vals[hi]-vals[lo])*(i-lo)
print(f"{day} at the course start (5,400 ft), {len(mem('temperature_2m'))} ensemble members, pulled {os.path.getmtime(f'{D}/ensemble.json') and __import__('datetime').datetime.utcfromtimestamp(os.path.getmtime(f'{D}/ensemble.json')).strftime('%Y-%m-%d %H:%MZ')}")
print('time   temp 10/50/90   feels   wind 10/50/90  dir   cloud med   wet members')
for hh in ['05:00','07:00','08:00','09:00','10:00','11:00']:
    i=t.index(f'{day}T{hh}')
    T=[e[k][i] for k in mem('temperature_2m')]; A=[e[k][i] for k in mem('apparent_temperature')]; W=[e[k][i] for k in mem('wind_speed_10m')]; C=[e[k][i] for k in mem('cloud_cover')]; P=[e[k][i] for k in mem('precipitation')]
    dirs=[e[k][i] for k in mem('wind_direction_10m') if e[k][i] is not None]
    md=(math.degrees(math.atan2(sum(math.sin(math.radians(v)) for v in dirs),sum(math.cos(math.radians(v)) for v in dirs)))+360)%360
    print(f"{hh}   {pct(T,.1):3.0f}/{pct(T,.5):3.0f}/{pct(T,.9):3.0f}     {pct(A,.5):3.0f}     {pct(W,.1):2.0f}/{pct(W,.5):2.0f}/{pct(W,.9):2.0f}     {md:3.0f}°   {pct(C,.5):3.0f}%      {sum(1 for v in P if v and v>0)}/{len(P)}")
idx=[i for i,x in enumerate(t) if x[:10] in ('2026-10-22','2026-10-23')]
tot=[sum((e[k][i] or 0) for i in idx) for k in mem('precipitation')]
print(f"Thu+Fri rain at the course: {pct(tot,.1):.2f}/{pct(tot,.5):.2f}/{pct(tot,.9):.2f} in; members over 0.25 in: {sum(1 for v in tot if v>0.25)}/{len(tot)}")
sat=[i for i,x in enumerate(t) if x.startswith(day)]
mx=[max(e[k][i] for i in sat) for k in mem('temperature_2m')]; mn=[min(e[k][i] for i in sat) for k in mem('temperature_2m')]
print(f"Daily high 10/50/90: {pct(mx,.1):.0f}/{pct(mx,.5):.0f}/{pct(mx,.9):.0f}   low: {pct(mn,.1):.0f}/{pct(mn,.5):.0f}/{pct(mn,.9):.0f}")
l=json.load(open(f'{D}/latest.json'))['daily']
for i,x in enumerate(l['time']):
    if '2026-10-21'<=x<='2026-10-25': print(f"best_match {x}: hi {l['temperature_2m_max'][i]:.0f} lo {l['temperature_2m_min'][i]:.0f} rain {l['precipitation_sum'][i]:.2f} in p{l['precipitation_probability_max'][i]:.0f} wind {l['wind_speed_10m_max'][i]:.0f} gust {l['wind_gusts_10m_max'][i]:.0f}")
