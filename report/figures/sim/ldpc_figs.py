"""LDPC figures of the SSCC baseline:
  ldpc_base.png      base matrix of the 802.11n rate-2/3 code (n = 1944, Z = 81): shift of every non-zero 81x81 block
                     (src/hw/rx/rtl/ps/ldpc_r23_pkg.sv via src/model/ldpc_ref.py)
  ldpc_pipeline.png  overlapped layer pipeline of ldpc_dec, clock by clock over one iteration, from an xsim trace
                     (report/notes/figures/sscc_ldpc/ldpc_dec_trace.txt: cycle, control state, phase-1 run / issue /
                     layer / column, phase-2 write valid / column / layer; codeword at 15.5 dB, 5 iterations)

    python ldpc_figs.py
"""
import os
import sys

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

import plotstyle as S

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src", "model"))
import ldpc_ref as L                                            # noqa: E402

OUT = os.path.join(HERE, "..", "hw")
S.apply()
SH = L.base_matrix()
ORD = L.layer_orders(SH)

# ---------------------------------------------------------------- base matrix
fig, ax = plt.subplots(figsize=(6.3, 2.5))
for r in range(L.NL):
    for c in range(L.NB):
        s = SH[r, c]
        if s < 0:
            fc = "white"
        else:
            fc = S.SHADE_B if c < L.KB else S.SHADE_Y
        ax.add_patch(Rectangle((c, r), 1, 1, facecolor=fc, edgecolor="#bfbfbf", lw=0.5))
        if s >= 0:
            ax.text(c + 0.5, r + 0.52, str(s), ha="center", va="center", fontsize=6.5, color=S.INK)
ax.add_patch(Rectangle((0, 0), L.NB, L.NL, fill=False, edgecolor=S.INK, lw=0.8))
ax.plot([L.KB, L.KB], [0, L.NL], color=S.INK, lw=0.8)
ax.set_xlim(0, L.NB); ax.set_ylim(L.NL, -1.1)
ax.set_xticks(np.arange(L.NB) + 0.5); ax.set_xticklabels(range(L.NB), fontsize=6.5)
ax.set_yticks(np.arange(L.NL) + 0.5); ax.set_yticklabels([f"层{r}" for r in range(L.NL)], fontsize=7)
ax.tick_params(length=0)
ax.text(L.KB / 2, -0.45, "信息部分（16列）", ha="center", va="center", fontsize=7.5)
ax.text(L.KB + (L.NB - L.KB) / 2, -0.45, "校验部分（8列）", ha="center", va="center", fontsize=7.5)
ax.set_xlabel("块列 c（每列81个变量）", fontsize=7.5)
for sp in ax.spines.values():
    sp.set_visible(False)
S.save(fig, os.path.join(OUT, "ldpc_base.png"))

# ---------------------------------------------------------------- pipeline over one iteration
t = np.loadtxt(os.path.join(ROOT, "report", "notes", "figures", "sscc_ldpc", "ldpc_dec_trace.txt"), dtype=int)
cyc, cst, p1run, p1iss, p1l, p1col, wv, wcol, p2l = t.T
run = cst == 2
iss_idx = np.cumsum(p1iss & run) - 1                            # index of the issued block
it_of = np.where(p1iss & run, iss_idx // 88, -1)
IT = 2                                                          # a middle iteration
sel = np.flatnonzero(it_of == IT)
c0, c1 = cyc[sel[0]], cyc[sel[-1]] + 1
win = (cyc >= c0) & (cyc < c1 + 8)
cols = [S.BLUE, "#7fa3c8"]
fig, (ax, az) = plt.subplots(2, 1, figsize=(6.3, 3.4), gridspec_kw=dict(height_ratios=[1, 1.25]))


def draw(a, lo, hi, text):
    for i in np.flatnonzero((cyc >= lo) & (cyc < hi)):
        x = cyc[i] - c0
        if run[i] and p1iss[i]:
            a.add_patch(Rectangle((x, 1), 1, 0.8, facecolor=cols[p1l[i] % 2], edgecolor="white", lw=0.3))
            if text:
                a.text(x + 0.5, 1.4, str(p1col[i]), ha="center", va="center", fontsize=5.5, color="white")
        elif run[i] and p1run[i]:
            a.add_patch(Rectangle((x, 1), 1, 0.8, facecolor="#d9d9d9", edgecolor="white", lw=0.3))
        if wv[i]:
            a.add_patch(Rectangle((x, 0), 1, 0.8, facecolor=[S.ORANGE, "#e6a57e"][p2l[i] % 2], edgecolor="white", lw=0.3))
            if text:
                a.text(x + 0.5, 0.4, str(wcol[i]), ha="center", va="center", fontsize=5.5, color="white")
    a.set_yticks([0.4, 1.4]); a.set_yticklabels(["第二阶段（写）", "第一阶段（读）"], fontsize=7)
    a.tick_params(axis="y", length=0)
    for sp in ("top", "right", "left"):
        a.spines[sp].set_visible(False)


draw(ax, c0, c1 + 8, False)
ax.set_xlim(0, c1 + 8 - c0); ax.set_ylim(-0.2, 2.6)
# layer labels above phase 1
for l in range(L.NL):
    xs = cyc[sel[l * 11:(l + 1) * 11]] - c0
    ax.text((xs[0] + xs[-1] + 1) / 2, 2.15, f"层{l}", ha="center", va="center", fontsize=6.5)
ax.set_xlabel(f"时钟（第{IT + 1}次迭代，共{c1 - c0}个时钟；灰色为第一阶段等待记分板）", fontsize=7)
# zoom: end of layer 1 / layer 2 with column numbers
z0 = cyc[sel[11 + 6]]; z1 = cyc[sel[33]] + 1
ax.add_patch(Rectangle((z0 - c0, -0.15), z1 - z0, 2.05, fill=False, edgecolor=S.GRAY, lw=0.7))
draw(az, z0, z1, True)
az.set_xlim(z0 - c0, z1 - c0); az.set_ylim(-0.2, 1.9)
az.set_xlabel("局部放大：块内数字为列号（同一列写回后下一层才能读）", fontsize=7)
fig.tight_layout()
S.save(fig, os.path.join(OUT, "ldpc_pipeline.png"))
print("iteration", IT + 1, "clocks", c1 - c0, "stall clocks", int(np.sum(run & p1run.astype(bool) & ~p1iss.astype(bool) & (cyc >= c0) & (cyc < c1))))
