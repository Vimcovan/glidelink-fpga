"""64-QAM soft demapper of the SSCC receiver (qam64_softdemap in src/hw/rx/rtl/ps/sscc_rx.sv): per axis, the 7-bit LLRs
of the three Gray-coded bits vs the received value, with the exact LLR (log-sum, 18 dB SNR) scaled to the same slope at
the decision boundaries for comparison.

    python sscc_demap.py      -> ../hw/sscc_demap.png
"""
import os

import numpy as np
import matplotlib.pyplot as plt

import plotstyle as S

HERE = os.path.dirname(os.path.abspath(__file__))
S.apply()
A = 158
LV = np.array([-7, -5, -3, -1, 1, 3, 5, 7]) * A
GRAY = ["000", "001", "011", "010", "110", "111", "101", "100"]       # b0 b1 b2 of each level, left to right

y = np.linspace(-8 * A, 8 * A, 4001)
ay = np.abs(y)
D = [y, 4 * A - ay, 2 * A - np.abs(ay - 4 * A)]                       # > 0: bit 1
Lq = [-np.sign(d) * np.minimum(np.floor(np.abs(d) / 8), 63) for d in D]

# exact per-axis LLR ln P(b=0)/P(b=1) at 18 dB (sigma^2 per axis = Es / (2 SNR), Es = 42 A^2)
s2 = 42 * A * A / 2 / 10 ** 1.8
lik = np.exp(-(y[:, None] - LV[None, :]) ** 2 / (2 * s2))
bits = np.array([[int(g[i]) for g in GRAY] for i in range(3)])
Lx = [np.log(np.maximum((lik * (bits[i] == 0)).sum(1), 1e-300)) - np.log(np.maximum((lik * (bits[i] == 1)).sum(1), 1e-300))
      for i in range(3)]

fig, axs = plt.subplots(3, 1, figsize=(6.3, 4.6), sharex=True)
for i, ax in enumerate(axs):
    k = 1 / 8 / (2 * A / s2)          # exact LLR slope 2A/sigma^2 per unit of D -> D/8
    ax.plot(y / A, np.clip(Lx[i] * k, -63, 63), color=S.GRAY, lw=1.0, label="精确LLR（18 dB，按相同斜率缩放）")
    ax.plot(y / A, Lq[i], color=S.BLUE, lw=1.4, label="硬件输出（7位）")
    ax.axhline(0, color=S.INK, lw=0.5)
    for lv, g in zip(LV, GRAY):
        ax.plot(lv / A, 0, "o", ms=3.2, color=S.ORANGE, zorder=3)
        ax.text(lv / A, -82, g[i], ha="center", va="center", fontsize=7, color=S.ORANGE)
    ax.set_ylim(-95, 95); ax.set_yticks([-63, 0, 63])
    ax.set_ylabel(f"b{i} 的 LLR", fontsize=8)
    S.finish(ax)
    if i == 2:                        # b2 never saturates: zoom on the peak at y = 0, where the two curves differ
        z = ax.inset_axes([0.635, 0.56, 0.19, 0.40])    # above the b2 curve, which is negative for 2.2 < |y|/A < 5.8
        zs = np.abs(y / A) < 0.45
        z.plot(y[zs] / A, Lx[i][zs] * k, color=S.GRAY, lw=1.0)
        z.plot(y[zs] / A, Lq[i][zs], color=S.BLUE, lw=1.2)
        z.set_xlim(-0.4, 0.4); z.set_ylim(28, 42)
        z.set_xticks([-0.4, 0, 0.4]); z.set_xticklabels([]); z.set_yticks([30, 35, 40])
        z.tick_params(labelsize=6, length=2, pad=1)
        z.grid(color=S.GRID, lw=0.3)
        ax.indicate_inset_zoom(z, edgecolor="#7f7f7f", lw=0.6)
h, l = axs[0].get_legend_handles_labels()
fig.legend(h, l, loc="upper center", ncol=2, fontsize=7, frameon=False, bbox_to_anchor=(0.5, 1.0))
axs[-1].set_xticks(range(-7, 8, 2))
axs[-1].set_xlabel("接收值 y / A（A = 158；橙点为星座电平，数字为该电平上这一比特的值）", fontsize=7.5)
fig.tight_layout(rect=(0, 0, 1, 0.95))
S.save(fig, os.path.join(HERE, "..", "hw", "sscc_demap.png"))
