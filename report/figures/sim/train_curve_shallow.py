"""Training of the shallow decoder (data/model_shallow/checkpoint/training_*.csv): joint fine-tuning of the encoder and
the shallow decoder at 10 dB AWGN after the noiseless decoder stage (validation PSNR, DIV2K validation crops).

    python train_curve_shallow.py      -> ../results/train_curve_shallow.png
"""
import csv
import json
import os

import numpy as np
import plotstyle as S
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
CK = os.path.normpath(os.path.join(HERE, "..", "..", "..", "data", "model_shallow", "checkpoint"))
OUT = os.path.join(HERE, "..", "results")
S.apply()


def load(n):
    rows = list(csv.DictReader(open(os.path.join(CK, f"training_{n}.csv"))))
    return (np.array([float(r[k]) for r in rows]) for k in ("val_psnr", "deployed_val_psnr", "lr"))


# original (symmetric) decoder at 10 dB, W8A12 integer model: report/notes/figures/shallow_decoder/int_sweep.json
ref_int = json.load(open(os.path.join(HERE, "..", "..", "notes", "figures", "shallow_decoder", "int_sweep.json"),
                         encoding="utf-8"))["psnr_db"]["10"]["dep_int"]
stages = [("joint", "联合训练"), ("cont", "继续训练"), ("qat", "量化感知训练")]
fig, ax = plt.subplots(figsize=(6.3, 2.6), dpi=300)
x0 = 0; ref = None; xs, ys, lrs = [], [], []
for n, name in stages:
    v, d, lr = load(n)
    x = x0 + np.arange(1, len(v) + 1)
    xs.append(x); ys.append(v); lrs.append(lr); ref = d[-1]
    ax.axvline(x0, color=S.GRAY, lw=0.5)
    ax.text(x0 + len(v) / 2, 31.45, name, ha="center", fontsize=8, color=S.INK)
    x0 += len(v)
ax.plot(np.concatenate(xs), np.concatenate(ys), color=S.BLUE, lw=0.8, label="浅层解码器（验证集PSNR）")
nq = len(xs[0]) + len(xs[1])
ax.plot([0, nq], [ref, ref], color=S.ORANGE, lw=0.8, label=f"原解码器（浮点）{ref:.2f} dB")
ax.plot([nq, x0], [ref_int, ref_int], color=S.ORANGE, lw=1.4, ls=(0, (1, 0)), alpha=0.55,
        label=f"原解码器（W8A12整数）{ref_int:.2f} dB")
b = len(xs[0]) + len(xs[1]) + int(np.argmax(ys[2]))
ax.plot([b + 1], [ys[2].max()], "o", ms=3.5, color=S.ORANGE)
ax.annotate(f"部署检查点：{ys[2].max():.2f} dB", xy=(b + 1, ys[2].max()), xytext=(1180, 30.55), fontsize=8,
            arrowprops=dict(arrowstyle="->", lw=0.6, color=S.INK))
ax.set_xlim(0, x0); ax.set_ylim(29.9, 31.6)
ax.set_xlabel("训练轮数（10 dB AWGN）"); ax.set_ylabel("PSNR / dB")
ax.legend(loc="lower right", fontsize=8)
S.finish(ax, grid="y")
fig.tight_layout(); fig.savefig(os.path.join(OUT, "train_curve_shallow.png"), bbox_inches="tight", pad_inches=0.03)
print("best", b + 1, ys[2].max(), "ref", ref, "joint end", ys[0][-1], "cont best", ys[1].max())
