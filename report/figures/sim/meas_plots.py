"""Report versions (Chinese labels, report style) of the measurement figures; the data are those of
data/measurements, the MATLAB originals (board/tests/plot_*.m) stay next to the data.

    python meas_plots.py   -> ../results/psnr_snr.png, fps_10min.png, latency_cdf.png
"""
import json
import os

import numpy as np
import matplotlib.pyplot as plt

import plotstyle as S

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
MEAS = os.path.join(ROOT, "data", "measurements")
OUT = os.path.join(HERE, "..", "results")
S.apply()

# ---------------------------------------------------------------- PSNR vs data-aided SNR
da = json.load(open(os.path.join(MEAS, "psnr_snr", "k0802_shallow_da.json"), encoding="utf-8"))
EDGE = 0.05      # DeepJSCC-Q points at the sync edge (>= 5 % of the snapshots from false syncs) are left out
fig, ax = plt.subplots(figsize=(4.2, 3.0))
for mode, name, col, mk in (("jscc", "DeepJSCC-Q", S.BLUE, "o"), ("sscc", "SSCC\n（JPEG + 卷积码 + 64QAM）", S.ORANGE, ">")):
    pts = sorted((r["snr_da_db"], r["psnr_shown"]) for r in da["results"]
                 if r["mode"] == mode and r.get("snr_da_db") is not None and r.get("psnr_shown") is not None
                 and not (mode == "jscc" and (r.get("snr_da_excl") or 0) >= EDGE))
    x, y = np.array(pts).T
    ax.plot(x, y, "-" + mk, color=col, lw=1.1, ms=3.6, mfc="white", mew=0.9, label=name)
ax.set_ylim(7, 33.5); ax.set_yticks(range(8, 34, 4))
ax.set_xlim(5.5, 35.5); ax.set_xticks(range(6, 36, 3))
ax.set_xlabel("数据辅助 SNR / dB"); ax.set_ylabel("PSNR / dB")
ax.legend(loc="lower right", fontsize=7.5, handlelength=1.6)
S.finish(ax)
S.save(fig, os.path.join(OUT, "psnr_snr.png"))

# ---------------------------------------------------------------- frame rate over 10 minutes (PL frame counter)
# rx_server logs every 5 s the decoded-image rate from the PL counter IMG_FRAMES (fps/rx_server_10min.csv); the
# per-frame arrival times at a measurement process on the RX PS (fps_10min_shallow_*.csv) also contain the jitter
# of that process (CPU shared with the GUI), so the counter is the measure of the link frame rate
d = np.genfromtxt(os.path.join(MEAS, "fps", "rx_server_10min.csv"), delimiter=",", names=True)
t, f = d["t_s"] / 60, d["images_per_s"]
frames = int(d["img_frames"][-1] - d["img_frames"][0]); span = d["t_s"][-1] - d["t_s"][0]
fig, a1 = plt.subplots(figsize=(6.3, 1.9))
a1.plot(t, f, color=S.BLUE, lw=0.9, marker="o", ms=1.8)
a1.axhline(30, color=S.ORANGE, lw=0.8, ls="--")
a1.set_xlim(0, 10); a1.set_ylim(29, 31)
a1.set_xlabel("时间 / min"); a1.set_ylabel("帧率 / fps")
S.finish(a1)
print(f"fps: {frames} frames in {span:.0f} s = {frames / span:.2f} fps, 5 s rates {f.min()}..{f.max()}")
S.save(fig, os.path.join(OUT, "fps_10min.png"))

# ---------------------------------------------------------------- latency CDF of the three PL segments
names = [("encoding", "(a) 编码"), ("decoding", "(b) 解码"), ("transmission", "(c) 传输")]
fig, axs = plt.subplots(1, 3, figsize=(6.3, 2.0))
for ax, (key, title) in zip(axs, names):
    x = np.sort(np.loadtxt(os.path.join(MEAS, "latency", f"segments_3seg_{key}.csv"), ndmin=1))
    lo, hi = np.percentile(x, [0.5, 99.5]); pad = 0.12 * (hi - lo)
    for v in (lo, hi):
        ax.axvline(v, color=S.GRAY, lw=0.7, ls="--")
    ax.step(x, np.arange(1, x.size + 1) / x.size, where="post", color=S.BLUE, lw=1.1)
    ax.axvline(np.median(x), color=S.ORANGE, lw=0.7, ls=":")
    ax.set_xlim(lo - pad, hi + pad); ax.set_ylim(0, 1.02); ax.set_yticks([0, 0.5, 1])
    ax.set_xlabel(f"时延 / ms\n{title}，中位数 {np.median(x):.2f} ms")
    ax.set_ylabel("CDF" if ax is axs[0] else "")
    S.finish(ax)
    print(f"{key}: median {np.median(x):.3f}, p0.5 {lo:.3f}, p99.5 {hi:.3f}, n {x.size}")
fig.tight_layout()
S.save(fig, os.path.join(OUT, "latency_cdf.png"))
