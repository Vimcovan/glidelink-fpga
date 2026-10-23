"""Received DeepJSCC-Q constellations (equalizer output, 48 data subcarriers) at three attenuation points of the
over-the-air sweep in data/measurements/psnr_snr (k0802_final, deployed system), drawn like MATLAB's scatterplot
(Communications Toolbox): 4,000 randomly drawn received points per panel as blue dots, the reference 64QAM points as red stars ('r*'), square
axes in units of the constellation level A = 158 (ideal points at +-1, +-3, +-5, +-7).

    python const_plot.py      -> ../results/const_ota.png
"""
import json
import os

import numpy as np
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
A = 158.0                                           # 64QAM level step of the RTL (levels +-1, +-3, +-5, +-7 times A)
LV = np.arange(-7, 8, 2)
LIM = 9.5
N_SHOW = 4000                                       # points drawn per panel

da = json.load(open(os.path.join(MEAS, "k0802_final_da.json"), encoding="utf-8"))
snr = {(r["mode"], round(float(r["att"]), 2)): r for r in da["results"]}


def load(mode, att):
    d = np.load(os.path.join(MEAS, "constellations", f"{mode}_{att:05.2f}dB_p0.npz"))
    z = d["cpe"][:, :, DATA].reshape(-1) / A
    return z[np.random.default_rng(1).choice(len(z), N_SHOW, replace=False)]   # all ~75,000 points would merge


points = [0.0, 31.0, 46.0]
fig, axs = plt.subplots(1, 3, figsize=(6.3, 2.45))
gi, gq = np.meshgrid(LV, LV)
for k, (ax, att) in enumerate(zip(axs, points)):
    z = load("jscc", att)
    r = snr.get(("jscc", att), {})
    ax.plot(z.real, z.imag, ".", ms=2.6, mew=0, color="#0072BD", rasterized=True)     # MATLAB's default blue
    ax.plot(gi.ravel(), gq.ravel(), "*", ms=3.2, mew=0.5, color="#D62728")             # reference points ('r*')
    ax.set_xlim(-LIM, LIM); ax.set_ylim(-LIM, LIM); ax.set_aspect("equal")
    ax.set_xticks([-8, -4, 0, 4, 8]); ax.set_yticks([-8, -4, 0, 4, 8])
    ax.tick_params(labelsize=6.5, length=2, pad=1.5)
    ax.set_xlabel("同相", fontsize=7, labelpad=1)
    if k == 0:
        ax.set_ylabel("正交", fontsize=7, labelpad=1)
    for s in ax.spines.values():
        s.set_linewidth(0.6)
    ax.set_title(f"数据辅助 SNR {r['snr_da_db']:.1f} dB\n解码 PSNR {r['psnr_shown']:.2f} dB", fontsize=7.5)
fig.tight_layout(w_pad=0.6)
S.save(fig, os.path.join(OUT, "const_ota.png"))
