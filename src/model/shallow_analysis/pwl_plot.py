"""Learned PWL activations of B2-PWL (joint): 2 blocks x 32 channels, with the distribution of their inputs
(DIV2K validation, 10 dB, joint encoder) and the initial LeakyReLU."""
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402

sys.path.insert(0, 'D:/ClaudePrj/deepjsccq-ofdm-fpga/src/model')
import train_shallow_dec as T  # noqa: E402

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
dev = 'cuda'
R = Path('D:/ClaudePrj/DeepJSCC-Q/runs_shallow')
ck = torch.load(R / 'joint_sp_k3_n24_b2pwl/best.pt', map_location=dev, weights_only=False)
dec = T.build({**ck['args']}).to(dev).eval(); dec.load_state_dict(ck['decoder'])
enc = T.load_encoder(T.REPO / 'data/model/checkpoint/best.pt', dev); enc.encoder.load_state_dict(ck['encoder'])
val = torch.from_numpy(np.load('D:/ClaudePrj/DeepJSCC-Q/data/cache/val_center256.npy')).permute(0, 3, 1, 2).to(dev)
acts = [m for m in dec.modules() if isinstance(m, T.PWLAct)]

# inputs of each PWL activation: histogram per channel
bins = np.linspace(-6, 6, 241)
hist = [np.zeros((32, len(bins) - 1)) for _ in acts]
outside = [np.zeros(32) for _ in acts]; total = [0 for _ in acts]
lo_hi = [[np.inf * np.ones(32), -np.inf * np.ones(32)] for _ in acts]


def hook(i):
    def f(m, inp, out):
        x = inp[0].detach()                               # (B, 32, H, W)
        xc = x.transpose(0, 1).reshape(32, -1)
        for c in range(32):
            hist[i][c] += torch.histc(xc[c].clamp(-6, 6), bins=len(bins) - 1, min=-6, max=6).cpu().numpy()
        outside[i] += (xc.abs() > 2).sum(1).cpu().numpy(); total[i] += xc.shape[1]
        lo_hi[i][0] = np.minimum(lo_hi[i][0], xc.min(1).values.cpu().numpy())
        lo_hi[i][1] = np.maximum(lo_hi[i][1], xc.max(1).values.cpu().numpy())
    return f


hs = [a.register_forward_hook(hook(i)) for i, a in enumerate(acts)]
with torch.no_grad(), torch.random.fork_rng(devices=[dev]):
    torch.manual_seed(4321)
    for i in range(0, len(val), 25):
        x = val[i:i + 25].float() / 255
        dec(T.channel_symbols(enc, x, 10.0))
for h in hs:
    h.remove()

plt.rcParams.update({'font.family': ['Microsoft YaHei', 'sans-serif'], 'font.size': 8, 'axes.edgecolor': '#c3c2b7',
                     'xtick.color': '#52514e', 'ytick.color': '#52514e'})
BG = '#fcfcfb'
xs = torch.linspace(-6, 6, 601, device=dev)
for bi, a in enumerate(acts):
    with torch.no_grad():
        y = a(xs.view(1, 1, -1, 1).repeat(1, 32, 1, 1)).squeeze(-1)[0].cpu().numpy()     # (32, 601)
    leaky = np.where(xs.cpu().numpy() > 0, xs.cpu().numpy(), xs.cpu().numpy() / 128)
    fig, axs = plt.subplots(4, 8, figsize=(17, 9.4), sharex=True); fig.patch.set_facecolor(BG)
    for c, ax in enumerate(axs.flat):
        ax.set_facecolor(BG)
        h = hist[bi][c]; h = h / h.max()
        # restrict the y range to the curve over the range the data occupies
        cdf = np.cumsum(hist[bi][c]) / hist[bi][c].sum()
        x_lo, x_hi = bins[np.searchsorted(cdf, 0.001)], bins[min(np.searchsorted(cdf, 0.999) + 1, len(bins) - 1)]
        sel = (xs.cpu().numpy() >= x_lo - 0.5) & (xs.cpu().numpy() <= x_hi + 0.5)
        ymin, ymax = y[c][sel].min(), y[c][sel].max(); pad = 0.1 * (ymax - ymin + 1e-6)
        ax2 = ax.twinx(); ax2.fill_between(bins[:-1], 0, h, step='post', color='#e6e5e0', zorder=0); ax2.set_ylim(0, 1.05)
        ax2.set_yticks([]); ax.set_zorder(ax2.get_zorder() + 1); ax.patch.set_visible(False)
        ax.plot(xs.cpu().numpy(), leaky, color='#9a9890', lw=1, ls='--')
        ax.plot(xs.cpu().numpy(), y[c], color='#2a78d6', lw=1.8)
        for v in (-2, 2):
            ax.axvline(v, color='#c3c2b7', lw=0.8, ls=':')
        ax.set_xlim(max(-6, x_lo - 0.5), min(6, x_hi + 0.5)); ax.set_ylim(ymin - pad, ymax + pad)
        ax.set_title(f'通道 {c}：|x|>2 占 {outside[bi][c] / total[bi] * 100:.1f} %', fontsize=8, loc='left')
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)
    fig.suptitle(f'B2-PWL 第 {bi + 1} 个逐点块学到的激活函数（32 个通道）：蓝线为学到的分段线性函数，灰虚线为初始的 LeakyReLU，'
                 f'浅灰底为该通道输入的分布（DIV2K 验证集，10 dB），点线为折点范围 ±2', x=0.01, ha='left', fontsize=10.5)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(OUT / f'pwl_block{bi + 1}.png', dpi=130, facecolor=BG)
    frac = outside[bi] / total[bi]
    print(f'block {bi + 1}: inputs outside ±2: mean {frac.mean() * 100:.1f} %, max {frac.max() * 100:.1f} %; '
          f'input range overall {lo_hi[bi][0].min():.2f} .. {lo_hi[bi][1].max():.2f}')
