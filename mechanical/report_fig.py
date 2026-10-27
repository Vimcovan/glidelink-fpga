"""Report figure of the enclosure (section 2.2.2): (a) base with the boards fitted, lid left off; (b) the empty base;
(c) the four walls drawn to scale with their openings.

    python report_fig.py      -> ../report/figures/hw/enclosure.png
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
from PIL import Image

import enclosure as E
import render as R

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "report", "figures", "sim"))
import plotstyle as S                                   # noqa: E402

S.apply()
OUT = os.path.join(HERE, "..", "report", "figures", "hw", "enclosure.png")
TMP = os.path.join(E.OUT, "_fig_a.png"), os.path.join(E.OUT, "_fig_b.png")

base = (E.to_trimesh(E.base()), np.array([0.80, 0.82, 0.86]))
R.render([base] + R.stand_ins(), TMP[0], az=-140, el=32, size=(1400, 1040))
R.render([base], TMP[1], az=40, el=32, size=(1400, 1040))

WALLS = [("back", "后侧板（从盒内看）"), ("right", "右侧板（从外看）"), ("left", "左侧板（从外看）"),
         ("front", "前侧板（从外看）")]
NAMES = {"HDMI in/out": "HDMI", "mini DP": "mini DP", "USB 3.0 micro": "USB", "USB 3.0 x4": "USB ×4",
         "microSD": "microSD", "UART": "UART", "TPM-Pmod (top entry)": "Pmod", "12 V in + switch": "电源与开关",
         "MIC/HP": "音频", "LINE-IN": "音频", "Pmod x2": "Pmod ×2"}


def wall(ax, w, title):
    a0, a1 = (E.X_OUT0, E.X_OUT1) if w in ("back", "front") else (E.Y_OUT0, E.Y_OUT1)
    flip = w in ("right", "front")
    A = (lambda a: a1 - a) if flip else (lambda a: a - a0)
    L = a1 - a0
    ax.add_patch(Rectangle((0, 0), L, E.H_WALL, fill=True, fc="#e6e9ee", ec=S.INK, lw=0.6))
    wins = sorted([(A(b0), A(b1), z0, z1, NAMES.get(n, n)) for ww, b0, b1, z0, z1, n in E.WINDOWS if ww == w],
                  key=lambda t: min(t[0], t[1]))
    for k, (pa, pb, z0, z1, lab) in enumerate(wins):              # labels in the wall below the openings,
        xa, xb = sorted((pa, pb))                                 # two staggered rows
        ax.add_patch(Rectangle((xa, E.Z_PCB + z0), xb - xa, z1 - z0, fc="white", ec=S.INK, lw=0.5))
        ax.text((xa + xb) / 2, E.Z_PCB - 4.6 - 4.4 * (k % 2), lab, fontsize=5, ha="center", va="center")
    for ww, b0, b1, z0, z1 in E.VENTS:
        if ww != w:
            continue
        b = b0
        while b + E.VENT_W <= b1:
            xa, xb = sorted((A(b), A(b + E.VENT_W)))
            ax.add_patch(Rectangle((xa, E.Z_PCB + z0), xb - xa, z1 - z0, fc="white", ec=S.INK, lw=0.3))
            b += E.VENT_PITCH
    if w == "front":
        xa, xb = sorted((A(E.SMA_SLOT_X[0]), A(E.SMA_SLOT_X[1])))
        z0 = E.SMA_Z - E.SMA_SLOT_W / 2
        ax.add_patch(Rectangle((xa, z0), xb - xa, E.H_WALL - z0 + 0.4, fc="white", ec="none"))  # open at the top
        ax.plot([xa, xa, xb, xb], [E.H_WALL, z0, z0, E.H_WALL], color=S.INK, lw=0.5)
        ax.text((xa + xb) / 2, z0 + 1.2, "SMA（槽口向上）", fontsize=5, ha="center", va="bottom")
    ax.plot([0, L], [E.Z_PCB] * 2, color=S.BLUE, lw=0.6, ls="--")
    ax.set_xlim(-2, 212); ax.set_ylim(-4, E.H_WALL + 4); ax.set_aspect("equal"); ax.axis("off")
    ax.set_title(title, fontsize=7, pad=2)


fig = plt.figure(figsize=(6.3, 5.6))
for k, (p, lab) in enumerate(zip(TMP, ("(a) 装入板卡（顶盖未画出）", "(b) 底壳"))):
    ax = fig.add_axes([0.0 + 0.5 * k, 0.47, 0.5, 0.5]); ax.imshow(Image.open(p)); ax.axis("off")
    ax.text(0.5, -0.02, lab, transform=ax.transAxes, ha="center", va="top", fontsize=8)
for k, (w, t) in enumerate(WALLS):
    ax = fig.add_axes([0.02 + 0.5 * (k % 2), 0.235 - 0.2 * (k // 2), 0.46, 0.2])
    wall(ax, w, t)
fig.text(0.5, 0.012, "(c) 侧板开孔（同一比例，蓝色虚线为PYNQ-ZU的PCB上表面）", ha="center", fontsize=8)
S.save(fig, OUT)
for p in TMP:
    os.remove(p)
