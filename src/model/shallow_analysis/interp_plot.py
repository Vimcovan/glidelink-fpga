"""Figure for the straight-path linearity test: (a) median path shape (along-chord position vs distance from the
chord, both / span), (b) length / chord with interquartile range. Same 100 DIV2K pairs as interp_test.py."""
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402

sys.path.insert(0, 'D:/ClaudePrj/deepjsccq-ofdm-fpga/src/model')
import train_shallow_dec as T  # noqa: E402
from deepjsccq_model import iq_to_latent, latent_to_iq  # noqa: E402

OUT = Path(sys.argv[1])
dev = 'cuda'
R = Path('D:/ClaudePrj/DeepJSCC-Q/runs_shallow')
dep = T.load_encoder(T.REPO / 'data/model/checkpoint/best.pt', dev)
ck = torch.load(R / 'joint_sp_k3_n24_pre4/best.pt', map_location=dev, weights_only=False)
jenc = T.load_encoder(T.REPO / 'data/model/checkpoint/best.pt', dev); jenc.encoder.load_state_dict(ck['encoder'])
sdec = T.build({**ck['args']}).to(dev).eval(); sdec.load_state_dict(ck['decoder'])
val = torch.from_numpy(np.load('D:/ClaudePrj/DeepJSCC-Q/data/cache/val_center256.npy')).permute(0, 3, 1, 2).to(dev)
g = torch.Generator().manual_seed(7)
pairs = [tuple(torch.randperm(len(val), generator=g)[:2].tolist()) for _ in range(100)]
ts = torch.linspace(0, 1, 11, device=dev)

NAMES = ['部署版编码器（量化前）', '部署版编码器（64QAM 量化后）', '联合微调后的编码器（pre 4）',
         '部署版解码器', '浅层解码器（pre 4）']


def shape(curve):
    c = curve.flatten(1).double()
    v = c[-1] - c[0]; span2 = (v * v).sum()
    rel = c - c[0]
    along = (rel @ v) / span2
    perp = (rel - along[:, None] * v[None]).norm(dim=1) / span2.sqrt()
    length = (c[1:] - c[:-1]).norm(dim=1).sum() / span2.sqrt()
    return along.cpu().numpy(), perp.cpu().numpy(), float(length)


acc = {n: [] for n in NAMES}
with torch.no_grad():
    for i0, i1 in pairs:
        x0, x1 = val[i0:i0 + 1].float() / 255, val[i1:i1 + 1].float() / 255
        xt = x0 + ts[:, None, None, None] * (x1 - x0)
        z = dep.encoder(xt)
        acc[NAMES[0]].append(shape(z))
        acc[NAMES[1]].append(shape(iq_to_latent(latent_to_iq(dep.quantizer(z)), *z.shape[1:])))
        acc[NAMES[2]].append(shape(jenc.encoder(xt)))
        for name, m, d in ((NAMES[3], dep, dep.decoder), (NAMES[4], jenc, sdec)):
            y = torch.cat([iq_to_latent(latent_to_iq(m.quantizer(m.encoder(x))), 16, 64, 64) for x in (x0, x1)])
            acc[name].append(shape(d(y[0:1] + ts[:, None, None, None] * (y[1:2] - y[0:1]))))

plt.rcParams.update({'font.family': ['Microsoft YaHei', 'sans-serif'], 'font.size': 10,
                     'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': '#52514e', 'xtick.color': '#52514e',
                     'ytick.color': '#52514e', 'text.color': '#0b0b0b'})
# fixed categorical order (reference palette): encoders blue/orange/aqua, decoders yellow/magenta
COL = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4']
MRK = ['o', 's', '^', 'D', 'v']
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 6.0), gridspec_kw={'width_ratios': [1.15, 1]})
fig.patch.set_facecolor('#fcfcfb')
for ax in (ax1, ax2):
    ax.set_facecolor('#fcfcfb')
    for sp in ('top', 'right'):
        ax.spines[sp].set_visible(False)

# (a) median path shape
ax1.axhline(0, color='#52514e', lw=1.2, ls='--', zorder=1)
ax1.text(0.5, 0.012, '完全线性：路径就是两端点之间的直线', ha='center', va='bottom', color='#52514e', fontsize=9)
for n, c, m in zip(NAMES, COL, MRK):
    al = np.median(np.stack([a[0] for a in acc[n]]), 0); pp = np.median(np.stack([a[1] for a in acc[n]]), 0)
    ax1.plot(al, pp, color=c, lw=2, marker=m, ms=7, mec='#fcfcfb', mew=1.2, label=n, zorder=3)
ax1.set_xlabel('沿两端点连线的位置（0 = 第一张图，1 = 第二张图）')
ax1.set_ylabel('偏离连线的距离 / 两端点距离')
ax1.set_title('(a) 沿直线插值时的路径形状（100 对图的中位数）', loc='left', fontsize=11)
ax1.set_xlim(-0.05, 1.05); ax1.set_ylim(-0.03, 0.62)
ax1.grid(axis='y', color='#e6e5e0', lw=0.8); ax1.set_axisbelow(True)
ax1.legend(frameon=False, fontsize=9, loc='upper center', bbox_to_anchor=(0.5, -0.16), ncol=2)

# (b) length / chord, median with IQR
lr = [np.array([a[2] for a in acc[n]]) for n in NAMES]
med = [np.median(v) for v in lr]
lo = [np.median(v) - np.percentile(v, 25) for v in lr]; hi = [np.percentile(v, 75) - np.median(v) for v in lr]
yp = np.arange(len(NAMES))[::-1]
ax2.barh(yp, med, color=COL, height=0.6, xerr=[lo, hi], error_kw={'ecolor': '#52514e', 'elinewidth': 1.2, 'capsize': 3},
         edgecolor='#fcfcfb', linewidth=2, zorder=3)
ax2.axvline(1.0, color='#52514e', lw=1.2, ls='--', zorder=4)
ax2.text(1.02, yp[0] + 0.45, '1.0 = 完全线性', color='#52514e', fontsize=9, va='bottom')
for y, v, h in zip(yp, med, hi):
    ax2.text(v + h + 0.05, y, f'{v:.2f}', va='center', fontsize=10, color='#0b0b0b')
ax2.set_yticks(yp); ax2.set_yticklabels(NAMES)
ax2.set_xlim(0, 3.0)
ax2.set_xlabel('路径长度 / 两端点直线距离（中位数，误差线为 25%–75%）')
ax2.set_title('(b) 路径长度比', loc='left', fontsize=11)
ax2.grid(axis='x', color='#e6e5e0', lw=0.8); ax2.set_axisbelow(True)

fig.suptitle('编码器、解码器沿直线路径的线性度（DIV2K 验证集，100 对图）', x=0.01, ha='left', fontsize=13)
fig.tight_layout(rect=(0, 0, 1, 0.95))
fig.savefig(OUT, dpi=160, facecolor=fig.get_facecolor())
for n in NAMES:
    a = np.stack([x[1] for x in acc[n]])
    print(n, 'max perp median', np.median(a.max(1)).round(3), 'length', np.median([x[2] for x in acc[n]]).round(3))
