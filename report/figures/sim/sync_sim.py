"""Algorithm simulation of the RX synchronisation (frame_detection.sv, time_sync.sv) for the design report.

Signal: the TX preamble ROM (src/hw/tx/rtl/phy/ofdm_tx/preamble.mem) followed by random 64-QAM OFDM symbols, after an
idle gap; channel = worst-case CFO of two boards (+-20 ppm crystals -> 40 ppm at 915 MHz = 36.6 kHz), a timing offset
of half a sample (the sampling phases of the two boards are unrelated) and AWGN at 10 dB SNR.

  (a) frame detection: 16-lag autocorrelation P_n and energy E_n over 16 samples, metric |P_n| / E_n vs 0.5
  (b) LTF timing: normalised cross-correlation with the LTF, rho^2 = |X_n|^2 / (E_n E_c), integer vs half-sample offset,
      and the two-point sum rho^2(n-1) + rho^2(n) used by the RTL; the RTL test |X(n-1)|^2 + |X(n)|^2 > 0.375 E
      corresponds to rho^2 ~ 0.375 / 0.81 = 0.46 (RTL scaling, see time_sync.sv)

    python sync_sim.py      -> ../hw/fd_sim.png, ../hw/ts_sim.png
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
OUT = os.path.join(HERE, "..", "hw")
import plotstyle as S
S.apply()
INK, ACC, ACC2, GRID = S.INK, S.BLUE, S.ORANGE, S.GRID

rng = np.random.default_rng(7)
FS, F0 = 20e6, 915e6


def s12(x):
    return np.where(x > 2047, x - 4096, x)


def preamble():
    v = np.array([int(l, 16) for l in open(os.path.join(ROOT, "src/hw/tx/rtl/phy/ofdm_tx/preamble.mem")).read().split()])
    return (s12(v & 0xFFF) + 1j * s12(v >> 12)) / 1024.0


def ofdm_symbols(n):
    lv = np.array([-7, -5, -3, -1, 1, 3, 5, 7]) / np.sqrt(42)
    out = []
    used = [k for k in range(64) if not (k <= 5 or k >= 59 or k == 32)]
    for _ in range(n):
        X = np.zeros(64, complex)
        X[used] = rng.choice(lv, len(used)) + 1j * rng.choice(lv, len(used))
        x = np.fft.ifft(np.fft.ifftshift(X)) * 8
        out.append(np.r_[x[-16:], x])
    return np.concatenate(out)


def channel(x, frac, snr_db, cfo_hz):
    n = len(x)
    f = np.fft.fftfreq(n)
    y = np.fft.ifft(np.fft.fft(x) * np.exp(-2j * np.pi * f * frac))       # fractional delay
    y = y * np.exp(2j * np.pi * cfo_hz / FS * np.arange(n))
    p = np.mean(abs(x[x != 0]) ** 2)
    return y + np.sqrt(p / 10 ** (snr_db / 10) / 2) * (rng.standard_normal(n) + 1j * rng.standard_normal(n))


def slide(v, L):
    c = np.cumsum(np.r_[np.zeros(L), v])
    return c[L:] - c[:-L]


GAP = 400
pre = preamble()
tx = np.r_[np.zeros(GAP, complex), pre, ofdm_symbols(6)]
cfo = 40e-6 * F0
x = channel(tx, 0.5, 10, cfo)
n = np.arange(len(x)) - GAP                                    # sample index relative to the frame start

# ---------------------------------------------------------------- (a) frame detection
prod = x[16:] * np.conj(x[:-16])
P = np.r_[np.zeros(16 + 15), slide(prod, 16)[: len(x) - 31]]
E = np.r_[np.zeros(16 + 15), slide(abs(x[:-16]) ** 2, 16)[: len(x) - 31]]
m = np.where(E > 1e-9, abs(P) / np.maximum(E, 1e-12), 0)
E_THR = 16 * (101 / 1024) ** 2           # RTL energy gate: sum(|x|^2/1024) > 160 over 16 samples, i.e. rms > 101 of 2048
ok = (m > 0.5) & (E > E_THR)
run = np.zeros(len(x), int)
for i in range(1, len(x)):
    run[i] = run[i - 1] + 1 if ok[i] else 0
det = int(np.argmax(run >= 64))

fig, (ax, ax2) = plt.subplots(2, 1, figsize=(6.3, 3.6), dpi=300, sharex=True, gridspec_kw=dict(height_ratios=[3, 2]))
for a in (ax, ax2):
    a.axvspan(0, 160, color=S.SHADE_Y, zorder=0)
    a.axvspan(160, 320, color=S.SHADE_B, zorder=0)
    a.axvline(det - GAP, color=INK, lw=0.8, ls=":")
    a.grid(color=GRID, lw=0.4)
ax.plot(n, m, color=ACC, lw=0.9, label="归一化自相关 |P(n)| / E(n)")
ax.axhline(0.5, color=ACC2, lw=0.8, ls="--", label="门限 0.5")
ax.text(45, 1.05, "STF", ha="center", fontsize=8); ax.text(240, 1.05, "LTF", ha="center", fontsize=8)
ax.set_ylim(0, 1.2); ax.set_ylabel("|P(n)| / E(n)")
ax.legend(loc="upper right", fontsize=7.5, frameon=False)
ax2.plot(n, run, color=ACC, lw=0.9, label="连续满足两个条件的采样数")
ax2.axhline(64, color=ACC2, lw=0.8, ls="--", label="64")
ax2.annotate("确认检测", xy=(det - GAP, 64), xytext=(det - GAP + 50, 85), fontsize=8,
             arrowprops=dict(arrowstyle="->", lw=0.6, color=INK))
ax2.set_xlim(-150, 700); ax2.set_ylim(0, 110)
ax2.set_xlabel("采样序号（相对帧起点）"); ax2.set_ylabel("连续点数")
ax2.legend(loc="upper right", fontsize=7.5, frameon=False)
for a in (ax, ax2):
    S.finish(a, grid=None)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "fd_sim.png")); plt.close(fig)
cfo_est = np.angle(P[det]) / (2 * np.pi * 16) * FS

# ---------------------------------------------------------------- (b) LTF timing
c = pre[192:256]                                                # one LTF symbol (64 samples)
Ec = np.sum(abs(c) ** 2)


def rho2(sig):
    X = np.convolve(sig, np.conj(c[::-1]))[: len(sig)]
    Er = np.r_[np.zeros(63), slide(abs(sig) ** 2, 64)][: len(sig)]
    return abs(X) ** 2 / np.maximum(Er * Ec, 1e-12)


r_int = rho2(channel(tx, 0.0, 10, cfo))
r_half = rho2(x)
r_pair = r_half + np.r_[0.0, r_half[:-1]]                       # rho^2(n-1) + rho^2(n)
thr = 0.375 / 0.81
fig, ax = plt.subplots(figsize=(6.3, 2.5), dpi=300)
ax.plot(n, r_int, color=INK, lw=0.7, alpha=0.6, label="整数采样对齐")
ax.plot(n, r_half, color=ACC, lw=0.9, label="偏移半个采样（最坏情况）")
ax.plot(n, r_pair, color="#2e8b57", lw=0.9, label="偏移半个采样，相邻两点之和（RTL判决量）")
ax.axhline(thr, color=ACC2, lw=0.8, label="门限 0.375E（≈ ρ² 0.46）")
ax.axvspan(160, 320, color=S.SHADE_B, zorder=0)
for k, lab in ((255, "LTF1 末点"), (319, "LTF2 末点")):
    ax.annotate(lab, xy=(k, r_half[k + GAP]), xytext=(k + 25, 0.9), fontsize=8,
                arrowprops=dict(arrowstyle="->", lw=0.6, color=INK))
ax.set_xlim(100, 700); ax.set_ylim(0, 1.1)
ax.set_xlabel("采样序号（相对帧起点）"); ax.set_ylabel("ρ² = |X(n)|² / (E(n)·Ec)")
ax.grid(color=GRID, lw=0.4)
ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=2, fontsize=7.5, frameon=False)
# zoom on the LTF1 peak: sample by sample
z = ax.inset_axes([0.60, 0.17, 0.37, 0.43])
zs = slice(GAP + 248, GAP + 264)
z.plot(n[zs], r_int[zs], "o-", color=INK, lw=0.7, ms=2.5, alpha=0.7)
z.plot(n[zs], r_half[zs], "s-", color=ACC, lw=0.8, ms=2.5)
z.plot(n[zs], r_pair[zs], "^-", color="#2e8b57", lw=0.8, ms=2.5)
z.axhline(thr, color=ACC2, lw=0.7)
z.set_xlim(248, 263); z.set_ylim(0, 1.05); z.set_xticks(range(248, 264, 3))
pk = GAP + 255
z.annotate(f"{r_int[pk]:.2f}", xy=(255, r_int[pk]), xytext=(252.9, 0.86), fontsize=6.5, color=INK)
z.annotate(f"{r_pair[pk + 1]:.2f}", xy=(256, r_pair[pk + 1]), xytext=(256.5, 0.93), fontsize=6.5, color="#2e8b57")
z.annotate(f"{r_half[pk]:.2f}", xy=(255, r_half[pk]), xytext=(252.3, 0.60), fontsize=6.5, color=ACC)
z.annotate(f"{r_half[pk + 1]:.2f}", xy=(256, r_half[pk + 1]), xytext=(256.6, 0.50), fontsize=6.5, color=ACC)
z.tick_params(labelsize=6.5); z.grid(color=GRID, lw=0.3)
z.set_title("LTF1 相关峰（逐采样）", fontsize=7, pad=2)
ax.indicate_inset_zoom(z, edgecolor="#7f7f7f", lw=0.6)
S.finish(ax, grid=None)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "ts_sim.png")); plt.close(fig)
print(f"detect at {det - GAP}, cfo est {cfo_est:.0f} Hz / {cfo:.0f} Hz, "
      f"LTF1 peak rho2 int {r_int[255 + GAP]:.2f} half {r_half[255 + GAP]:.2f} (max near {r_half[GAP + 240:GAP + 270].max():.2f}), "
      f"data max {r_half[GAP + 330:].max():.2f}, pair: peak {r_pair[GAP + 240:GAP + 270].max():.2f} data max {r_pair[GAP + 330:].max():.2f}")
