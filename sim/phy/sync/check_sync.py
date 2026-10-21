"""Compare a tb_sync log with the stimulus truth: per frame, where time_sync entered DATA relative to the first LTF1
sample (offsets relative to the median, i.e. 0 = LTF1, +64 = locked on LTF2), frames without sync, sync events that
match no frame (false triggers), and for gen_stim.py stimuli the CFO estimate error.

    python check_sync.py <tag>        reads truth_<tag>.txt and log_<tag>.txt
"""
import sys
from collections import defaultdict
import numpy as np

FS = 20e6
tag = sys.argv[1]
truth = [l.split() for l in open(f'truth_{tag}.txt')]
ts, cf = [], []
for l in open(f'log_{tag}.txt'):
    k, *v = l.split()
    if k == 'TS':
        ts.append(int(v[0]))
    elif k == 'CFO':
        cf.append((int(v[0]), int(v[1])))
replay = all(float(t[3]) == 0 for t in truth)          # gen_replay.py: column 2 = attenuation, no CFO truth
rows, used = [], set()
for t in truth:
    L = int(t[1])
    m = [x for x in ts if L - 400 <= x <= L + 400]
    used.update(m)
    e = [p for (x, p) in cf if L - 600 <= x <= L + 200]
    cfo_est = e[0] / 2 ** 15 * np.pi / (2 * np.pi * 16) * FS if e else None   # CORDIC phase, scaled radians fix18_15
    rows.append((t[2] if replay else 'all', m[0] - L if m else None, None if replay or cfo_est is None else cfo_est - float(t[3])))
ref = np.median([r[1] for r in rows if r[1] is not None])
groups = defaultdict(list)
for g, off, ce in rows:
    groups[g].append((None if off is None else int(off - ref), ce))
for g, v in groups.items():
    offs = [o for o, _ in v if o is not None]
    ce = [c for _, c in v if c is not None]
    line = (f'{g}: {len(v)} frames, timing ok (|off| <= 2) {sum(abs(o) <= 2 for o in offs)}, LTF2 (+64) {sum(abs(o - 64) <= 3 for o in offs)}, '
            f'other {sum(abs(o) > 2 and abs(o - 64) > 3 for o in offs)}, no sync {len(v) - len(offs)}')
    if ce:
        line += f', CFO error rms {np.sqrt(np.mean(np.square(ce))) / 1e3:.1f} kHz'
    print(line)
print(f'sync events matching no frame (false triggers): {len([x for x in ts if x not in used])}')
