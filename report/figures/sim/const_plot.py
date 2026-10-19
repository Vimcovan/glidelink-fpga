"""Received DeepJSCC-Q constellations (equalizer output, 48 data subcarriers) at three attenuation points of the
over-the-air sweep in data/measurements/psnr_snr (k0802_shallow, shallow-decoder system). Drawn as a 2-D histogram (log density) so that the
clusters stay visible where the points overlap; the ideal 64QAM points are marked.

    python const_plot.py      -> ../results/const_ota.png
"""
import json
import os

import numpy as np
from matplotlib.colors import LinearSegmentedColormap, LogNorm
import matplotlib.pyplot as plt

import plotstyle as S

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
MEAS = os.path.join(ROOT, "data", "measurements", "psnr_snr")
OUT = os.path.join(HERE, "..", "results")
os.makedirs(OUT, exist_ok=True)
S.apply()

NULLS = set(range(0, 6)) | {32} | set(range(59, 64))
PILOTS = [11, 25, 39, 53]
DATA = [k for k in range(64) if k not in NULLS and k not in PILOTS]
QAM = np.array([-1106, -790, -474, -158, 158, 474, 790, 1106])
LIM = 1500
CMAP = LinearSegmentedColormap.from_list("dens", ["#e8f0f7", "#7fa3c4", S.BLUE, "#0f2a44"])

da = json.load(open(os.path.join(MEAS, "k0802_shallow_da.json"), encoding="utf-8"))
snr = {(r["mode"], round(float(r["att"]), 2)): r for r in da["results"]}


def load(mode, att):
    d = np.load(os.path.join(MEAS, "constellations", f"{mode}_{att:05.2f}dB_p0.npz"))
    return d["cpe"][:, :, DATA].reshape(-1)


points = [0.0, 31.0, 42.0]
fig, axs = plt.subplots(1, 3, figsize=(6.3, 2.35))
bins = np.linspace(-LIM, LIM, 301)
gi, gq = np.meshgrid(QAM, QAM)
for ax, att in zip(axs, points):
    z = load("jscc", att)
    r = snr.get(("jscc", att), {})
    h, _, _ = np.histogram2d(z.real, z.imag, bins=[bins, bins])
    ax.imshow(np.ma.masked_equal(h.T, 0), origin="lower", extent=[-LIM, LIM, -LIM, LIM], cmap=CMAP,
              norm=LogNorm(vmin=1, vmax=h.max()), interpolation="nearest", rasterized=True)
    ax.plot(gi.ravel(), gq.ravel(), "+", ms=3.2, mew=0.6, color=S.ORANGE)
    ax.set_xlim(-LIM, LIM); ax.set_ylim(-LIM, LIM); ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_linewidth(0.6)
    ax.set_title(f"数据辅助 SNR {r['snr_da_db']:.1f} dB\n解码 PSNR {r['psnr_shown']:.2f} dB", fontsize=8.5)
fig.tight_layout()
S.save(fig, os.path.join(OUT, "const_ota.png"))
