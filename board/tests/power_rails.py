"""Mean power of the PYNQ-ZU supply rails (PMBus telemetry of the three IRPS5401 regulators, pynq.get_rails()).

    python3 power_rails.py --label jscc --seconds 60 [--out power.json]   (run as root, PYNQ venv)

Samples every rail at ~2 Hz and prints / appends the mean voltage, current and power per rail. Rail power is taken as
mean(V) x mean(I): the regulators report current in 1/64 A steps but power only in 1/32 W steps.
Groups: PL = INT (VCCINT, VCCINT_IO, VCCBRAM) + AUX (VCCAUX, VCCAUX_IO); PS = PSINTFP + PSINTLP + PSAUX;
DDR = PSDDR_HDMI (DDR4 1.2 V, shared with the HDMI buffer); the other rails supply board peripherals.
The AD9361 FMC card is powered from the FMC 12 V / 3V3 pins and is not on these rails.
"""
import argparse
import json
import os
import time

import pynq

GROUPS = {"PL": ["INT", "AUX"], "PS": ["PSINTFP", "PSINTLP", "PSAUX"], "DDR": ["PSDDR_HDMI"]}

ap = argparse.ArgumentParser()
ap.add_argument("--label", required=True)
ap.add_argument("--seconds", type=float, default=60)
ap.add_argument("--period", type=float, default=0.5)
ap.add_argument("--out", default="")
a = ap.parse_args()

rails = pynq.get_rails()
acc = {k: [0.0, 0.0, 0] for k in rails}
t_end = time.time() + a.seconds
while time.time() < t_end:
    for k, r in rails.items():
        try:
            acc[k][0] += r.voltage.value; acc[k][1] += r.current.value; acc[k][2] += 1
        except (AttributeError, OSError):
            pass
    time.sleep(a.period)

res = {}
for k, (v, i, n) in acc.items():
    if n:
        res[k] = {"V": round(v / n, 4), "A": round(i / n, 4), "W": round(v / n * i / n, 4), "n": n}
groups = {g: round(sum(res[r]["W"] for r in rs if r in res), 3) for g, rs in GROUPS.items()}
groups["chip"] = round(groups["PL"] + groups["PS"], 3)
groups["all_rails"] = round(sum(x["W"] for x in res.values()), 3)
rec = {"label": a.label, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": a.seconds, "groups": groups,
       "rails": res}
for k, x in sorted(res.items(), key=lambda kv: -kv[1]["W"]):
    print(f"{k:12s} {x['V']:6.3f} V {x['A']:7.3f} A {x['W']:7.3f} W")
print(a.label, groups, flush=True)
if a.out:
    old = json.load(open(a.out)) if os.path.exists(a.out) else []
    json.dump(old + [rec], open(a.out, "w"), indent=1)
