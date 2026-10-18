"""Large synthesis kernels of Yang & Mandt (ICCV 2023), fetched with tf_ckpt_fetch.py:
  1. spatial energy profile (sum over channels): share of the energy inside the central s x s block and per ring;
  2. how well a cascade of small transposed convolutions (3x3, stride 2, linear, wide channels) reproduces the
     large transposed convolution: fitted on the impulse responses, relative residual energy.

    python yang_kernels.py <two_layer.npz> <jpegl.npz> --out <figure.png> [--fit] [--controls]
    python yang_kernels.py <two_layer.npz> <jpegl.npz> --out x --report <fit.json> <report figure.png>
  3. controls: an iid random kernel, a random kernel with the trained kernel's spatial energy profile, and the trained
     kernel with its channels shuffled at every tap (same values and per-tap energy, no cross-tap structure).
TF Conv2DTranspose kernels are [kh, kw, out_ch, in_ch].
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[3] / 'report' / 'figures' / 'sim'))
import plotstyle as S                                          # noqa: E402
import matplotlib.pyplot as plt                                # noqa: E402


def kernel(npz, name):
    d = np.load(npz)
    k = [v for key, v in d.items() if name in key and key.endswith('kernel_ATTRIBUTES_VARIABLE_VALUE')]
    assert len(k) == 1, (name, list(d))
    return k[0].astype(np.float64)                             # [kh, kw, out, in]


def profile(k, s):
    e = (k ** 2).sum(axis=(2, 3))                              # spatial energy
    e = e / e.sum()
    n = e.shape[0]
    c = (n - 1) / 2
    yy, xx = np.mgrid[:n, :n]
    ring = np.maximum(np.abs(yy - c), np.abs(xx - c))           # Chebyshev distance from the centre
    lo = int(np.ceil(c - s / 2)); hi = lo + s
    centre = e[lo:hi, lo:hi].sum()
    rings = [e[(ring >= r - 0.5) & (ring < r + 0.5)].sum() / max(1, ((ring >= r - 0.5) & (ring < r + 0.5)).sum())
             for r in np.arange(0 if n % 2 else 0.5, c + 1)]
    return e, centre, np.array(rings)


def cascade_fit(k, stride, layers, width, steps=3000, dev='cuda'):
    """least-squares fit of the transposed conv (kernel k, stride) by `layers` linear 3x3 transposed convs of stride 2
    (product of the strides = stride), on impulse responses of every input channel; returns relative residual"""
    kh, _, cout, cin = k.shape
    assert 2 ** layers == stride
    tgt = torch.tensor(k.transpose(3, 2, 0, 1), dtype=torch.float32, device=dev)   # (cin, cout, kh, kw) response
    widths = [cin] + [width] * (layers - 1) + [cout]
    ws = [torch.nn.Parameter(0.05 * torch.randn(widths[i], widths[i + 1], 3, 3, device=dev)) for i in range(layers)]
    opt = torch.optim.Adam(ws, lr=3e-3)
    imp = torch.eye(cin, device=dev).view(cin, cin, 1, 1)
    size = 3
    for _ in range(layers - 1):
        size = (size - 1) * 2 + 3
    pad = size - kh                                             # response is size x size, target kh x kh
    best = None
    for off in range(0, pad + 1):
        t = F.pad(tgt, (off, pad - off, off, pad - off))
        for p in ws:
            torch.nn.init.normal_(p, std=0.05)
        opt = torch.optim.Adam(ws, lr=3e-3)
        sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, steps)
        for _ in range(steps):
            y = imp
            for w in ws:
                y = F.conv_transpose2d(y, w, stride=2)
            loss = ((y - t) ** 2).sum() / (t ** 2).sum()
            opt.zero_grad(); loss.backward(); opt.step(); sched.step()
        r = float(loss)
        best = r if best is None else min(best, r)
    return best, size


def controls(k):
    """kernels of the same shape that keep less and less of the trained kernel's structure"""
    rng = np.random.default_rng(0)
    e = (k ** 2).sum(axis=(2, 3), keepdims=True); e = e / e.mean()
    n = k.shape[0]
    shuffled = np.stack([np.stack([rng.permutation(k[i, j].ravel()).reshape(k.shape[2:]) for j in range(n)])
                         for i in range(n)])
    return {'iid random 13x13': rng.standard_normal(k.shape),
            'random, same spatial energy profile': rng.standard_normal(k.shape) * np.sqrt(e),
            'trained main, channels shuffled per tap': shuffled}


def report_figure(ks, fit_json, out):
    """(a), (b) spatial energy of the main 13x13 and the JPEG-like 18x18 kernels, (c) cascade-fit residuals"""
    res = json.load(open(fit_json, encoding='utf-8'))['residual_pct']
    fig, axs = plt.subplots(1, 3, figsize=(7.2, 2.5), dpi=300, gridspec_kw={'width_ratios': [1, 1, 2.4]})
    for ax, (name, k, s), t in zip(axs[:2], (ks[0], ks[2]), ('(a) 两层合成主路 13×13，步长 8', '(b) JPEG 式合成 18×18，步长 16')):
        e, _, _ = profile(k, s)
        ax.imshow(e / e.max(), cmap='viridis', vmin=0, vmax=1)
        n = e.shape[0]; lo = int(np.ceil((n - 1) / 2 - s / 2))
        ax.add_patch(plt.Rectangle((lo - 0.5, lo - 0.5), s, s, fill=False, ec='w', lw=0.7, ls='--'))
        ax.set_title(t, fontsize=6.5); ax.set_xticks([]); ax.set_yticks([])
    labels = {'two-layer main 13x13 (3 layers)': '训练核\n主路', 'two-layer residual 13x13 (3 layers)': '训练核\n残差路',
              'JPEG-like 18x18 (4 layers)': '训练核\nJPEG 式', 'iid random 13x13': '随机核\n',
              'random, same spatial energy profile': '随机核\n同能量分布',
              'trained main, channels shuffled per tap': '主路核\n打乱通道'}
    bx = axs[2]
    x = np.arange(len(labels))
    for off, w, c in ((-0.2, '32', S.LBLUE), (0.2, '128', S.BLUE)):
        v = [res[k][w] for k in labels]
        bx.bar(x + off, v, 0.38, color=c, label=f'中间层宽度 {w}')
        for xi, vi in zip(x + off, v):                         # value labels: the trained-kernel bars are tiny
            bx.text(xi, vi + 1.2, f'{vi:.2f}' if vi < 1 else f'{vi:.1f}', ha='center', va='bottom', fontsize=4.8,
                    color=S.INK)
    bx.set_ylim(0, 88)
    bx.set_xticks(x); bx.set_xticklabels(list(labels.values()), fontsize=5.5)
    bx.set_ylabel('拟合残差（% 核能量）', fontsize=6.5); bx.tick_params(axis='y', labelsize=6)
    bx.set_title('(c) 用 3×3 小核级联拟合大核的残差', fontsize=6.5)
    bx.legend(fontsize=5.5, loc='upper left')
    S.finish(bx, grid='y')
    fig.tight_layout(); fig.savefig(out, bbox_inches='tight', pad_inches=0.03)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('two_layer')
    ap.add_argument('jpegl')
    ap.add_argument('--out', required=True, help='energy-profile figure')
    ap.add_argument('--fit', action='store_true', help='cascade fits of the trained kernels')
    ap.add_argument('--controls', action='store_true', help='cascade fits of the control kernels')
    ap.add_argument('--report', nargs=2, metavar=('FIT_JSON', 'PNG'), help='report figure from the fit results')
    a = ap.parse_args()
    ks = [('两层合成：主路 13×13（步长 8）', kernel(a.two_layer, 'base_conv'), 8),
          ('两层合成：残差路 13×13（步长 8）', kernel(a.two_layer, '_synthesis_res'), 8),
          ('JPEG 式合成 18×18（步长 16）', kernel(a.jpegl, '_synthesis_conv'), 16)]
    S.apply()
    if a.report:
        report_figure(ks, *a.report)
        return
    fig, axs = plt.subplots(1, 4, figsize=(7.4, 1.95), dpi=300, gridspec_kw={'width_ratios': [1, 1, 1, 1.4]})
    for (name, k, s), ax, c in zip(ks, axs, (S.BLUE, S.ORANGE, S.GRAY)):
        e, centre, rings = profile(k, s)
        ax.imshow(e, cmap='viridis')
        n = e.shape[0]; lo = int(np.ceil((n - 1) / 2 - s / 2))
        ax.add_patch(plt.Rectangle((lo - 0.5, lo - 0.5), s, s, fill=False, ec='w', lw=0.6, ls='--'))
        ax.set_title(name, fontsize=6); ax.set_xticks([]); ax.set_yticks([])
        axs[3].plot(np.arange(len(rings)), rings / rings[0], color=c, marker='o', ms=2, lw=1, label=name.split('（')[0])
        print(f'{name}: centre {s}x{s} block holds {100 * centre:.1f} % of the energy; '
              f'ring energy per tap (rel. centre): ' + ' '.join(f'{v / rings[0]:.2f}' for v in rings))
    axs[3].set_xlabel('到中心的距离（抽头）', fontsize=6.5); axs[3].set_ylabel('每抽头能量（相对中心）', fontsize=6.5)
    axs[3].set_yscale('log'); axs[3].tick_params(labelsize=6); axs[3].legend(fontsize=5)
    S.finish(axs[3])
    fig.tight_layout(); fig.savefig(a.out, bbox_inches='tight', pad_inches=0.03)
    torch.manual_seed(0)
    if a.fit:
        for name, k, s in ks[:2]:
            for width in (32, 128):
                r, size = cascade_fit(k, s, 3, width)
                print(f'{name}: cascade of 3 linear 3x3 stride-2 transposed convs (width {width}, footprint {size}): '
                      f'residual {100 * r:.2f} % of the kernel energy')
        name, k, s = ks[2]
        for width in (32, 128):
            r, size = cascade_fit(k, s, 4, width)
            print(f'{name}: cascade of 4 (width {width}, footprint {size}): residual {100 * r:.2f} %')
    if a.controls:
        for name, c in controls(ks[0][1]).items():
            for width in (32, 128):
                r, _ = cascade_fit(c, 8, 3, width)
                print(f'{name}: width {width}: residual {100 * r:.2f} %', flush=True)


if __name__ == '__main__':
    main()
