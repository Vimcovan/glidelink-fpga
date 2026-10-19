"""Figures and numbers of the shallow-decoder write-up (report/notes/shallow_decoder_design.md), final model.

    python int_sweep.py ... --out report/notes/figures/shallow_decoder/int_sweep.json   (first)
    python report_figs.py --ckpt data/model_shallow/checkpoint/best.pt --cache <data/cache>
-> report/notes/figures/shallow_decoder/{interp_stages,pwl_final,psnr_snr_shallow}.png and the numbers
   printed (path statistics incl. the encoder, PWL shape statistics); the stage noise figure is in ablation_fig.py.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve()
REPO = HERE.parents[3]
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(REPO / 'report' / 'figures' / 'sim'))
import plotstyle as S                                          # noqa: E402
import matplotlib.pyplot as plt                                # noqa: E402
import quant_shallow as Q                                      # noqa: E402
from deepjsccq_model import iq_to_latent                       # noqa: E402
from train_shallow_dec import PWLAct, awgn, tx_iq              # noqa: E402
from interp_stages import front, tail                          # noqa: E402

OUT = REPO / 'report' / 'notes' / 'figures' / 'shallow_decoder'
TS = torch.linspace(0, 1, 11)


def dev_curve(curve):
    """per t: ||curve(t) - chord(t)|| / span, and the path length / span"""
    c = curve.flatten(1).double()
    ts = TS.to(c.device).double()
    chord = c[0][None] + ts[:, None] * (c[-1] - c[0])[None]
    span = (c[-1] - c[0]).norm()
    return ((c - chord).norm(dim=1) / span).cpu().numpy(), ((c[1:] - c[:-1]).norm(dim=1).sum() / span).item()


@torch.no_grad()
def fig_interp(enc, dec, val, dev):
    g = torch.Generator().manual_seed(7)
    pairs = [tuple(torch.randperm(len(val), generator=g)[:2].tolist()) for _ in range(100)]
    ts = TS.to(dev).view(-1, 1, 1, 1)
    keys = ['编码器（图像→潜变量）', '解码器前段（潜变量→残差块输出）', '解码器后段（残差块输出→sigmoid 前）',
            '整个解码器（潜变量→图像）', '编码器＋解码器（图像→64QAM→重建图像）']
    curves, lengths, extra = {k: [] for k in keys}, {k: [] for k in keys}, {}
    for i, j in pairs:
        x = val[[i, j]].float() / 255
        xt = (1 - ts) * x[0:1] + ts * x[1:2]
        for k, cv in ((keys[0], enc.encoder(xt)),):
            d, l = dev_curve(cv); curves[k].append(d); lengths[k].append(l)
        iq, shape = tx_iq(enc, x)
        y = iq_to_latent(iq, *shape)
        yt = (1 - ts) * y[0:1] + ts * y[1:2]
        h = front(dec, yt)
        ht = (1 - ts) * h[0:1] + ts * h[-1:]
        z, _ = tail(dec, ht)
        it, sh = tx_iq(enc, xt)                                # whole chain, noiseless: image -> 64-QAM -> image
        for k, cv in ((keys[1], h), (keys[2], z), (keys[3], dec(yt)), (keys[4], dec(iq_to_latent(it, *sh)))):
            d, l = dev_curve(cv); curves[k].append(d); lengths[k].append(l)
        d, l = dev_curve(dec(enc.encoder(xt)))                # chain without the 64-QAM decision (printed only)
        extra.setdefault('d', []).append(d); extra.setdefault('l', []).append(l)
    fig, ax = plt.subplots(figsize=(5.2, 3.6), dpi=300)
    cols = [S.GRAY, S.ORANGE, S.BLUE, S.LBLUE, S.INK]
    styles = ['--', '-', '-', ':', '-']
    for k, c, ls in zip(keys, cols, styles):
        m = np.median(np.array(curves[k]), 0)
        ax.plot(TS.numpy(), m, color=c, ls=ls, lw=1.3, marker='o', ms=2.5,
                label=f'{k}，路径长度 {np.median(lengths[k]):.2f}')
    ax.set_xlabel('插值系数 t')
    ax.set_ylabel('偏离弦线的距离 / 端点距离')
    ax.legend(fontsize=6.5, loc='upper center', bbox_to_anchor=(0.5, -0.2), ncol=2, columnspacing=1.0)
    S.finish(ax)
    fig.tight_layout(); fig.savefig(OUT / 'interp_stages.png', bbox_inches='tight', pad_inches=0.03)
    for k in keys:
        print(f'interp {k}: max deviation {np.median(np.max(curves[k], 1)):.3f}, length {np.median(lengths[k]):.3f}')
    print(f"interp chain without 64-QAM decision: max deviation {np.median(np.max(extra['d'], 1)):.3f}, "
          f"length {np.median(extra['l']):.3f}")


@torch.no_grad()
def fig_pwl(enc, dec, val, dev):
    acts = [m for m in dec.pre.modules() if isinstance(m, PWLAct)]
    # inputs of the activations (10 dB), for the shaded input range
    ins = {id(a): [] for a in acts}
    hooks = [a.register_forward_hook(lambda m, i, o: ins[id(m)].append(i[0].flatten().cpu())) for a in acts]
    torch.manual_seed(1)
    for b in range(0, 40, 20):
        x = val[b:b + 20].float() / 255
        iq, shape = tx_iq(enc, x)
        dec(iq_to_latent(awgn(iq, 10.0), *shape))
    for h in hooks:
        h.remove()
    u = torch.linspace(-3, 3, 601, device=dev).view(1, 1, -1, 1)
    xs = u.flatten().cpu().numpy()
    curves, rng, L, R = [], [], [], []
    for a in acts:
        c = a.a.shape[1]
        y = a(u.expand(1, c, -1, 1)).squeeze().cpu().numpy()          # (c, 601)
        lo, hi = np.percentile(torch.cat(ins[id(a)]).numpy(), [1, 99])
        sl = np.gradient(y, xs, axis=1)
        for k in range(c):
            curves.append(y[k]); rng.append((lo, hi))
            L.append(sl[k, (xs >= -2) & (xs <= -0.5)].mean()); R.append(sl[k, (xs >= 0.5) & (xs <= 2)].mean())
    L, R = np.array(L), np.array(R)
    eps = 0.05
    cls = np.where(R <= eps, 2, np.where(L < -eps, 0, 1))         # 0: V shape, 1: monotone rising, 2: falling right side
    names = ['V形（左侧斜率为负）', '单调上升', '右侧不上升']
    C = np.array(curves)
    lo = np.median([r[0] for r in rng]); hi = np.median([r[1] for r in rng])
    fig, ax = plt.subplots(figsize=(4.6, 3.0), dpi=300)
    for y in C:                                                 # all channels, faint: overlaps get darker
        ax.plot(xs, y, color=S.BLUE, lw=0.6, alpha=0.12, zorder=1)
    ax.plot([], [], color=S.BLUE, lw=0.8, alpha=0.4, label=f'各通道（{len(C)}条）')
    ax.plot(xs, C.mean(0), color=S.ORANGE, lw=1.8, zorder=3, label='平均')
    ax.plot(xs, np.where(xs > 0, xs, xs / 128), color=S.INK, lw=0.9, ls='--', zorder=2, label='LeakyReLU（初始值）')
    ax.set_xlim(-2.5, 2.5); ax.set_ylim(-1.2, 2.0)
    ax.set_xlabel('输入', fontsize=7.5); ax.set_ylabel('输出', fontsize=7.5)
    ax.tick_params(labelsize=6.5); ax.legend(fontsize=6.5, loc='upper left')
    S.finish(ax)
    fig.tight_layout(); fig.savefig(OUT / 'pwl_final.png', bbox_inches='tight', pad_inches=0.03)
    print(f'PWL: {len(L)} channels, left slope (-2..-0.5) median {np.median(L):.2f}, right (0.5..2) {np.median(R):.2f}, '
          f'negative left slope {np.mean(L < 0) * 100:.0f} %; classes ' +
          ', '.join(f'{names[q]} {int((cls == q).sum())}' for q in range(3)))

def fig_psnr(sweep_path):
    """sweep_path: int_sweep.json (int_sweep.py): float and W8A12 integer PSNR of both models, same noise"""
    d = json.load(open(sweep_path, encoding='utf-8'))['psnr_db']
    snr = np.array([float(k) for k in d])
    g = lambda key: np.array([d[k][key] for k in d])
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(4.4, 3.9), dpi=300, sharex=True,
                                 gridspec_kw={'height_ratios': [2, 1.3]})
    ax.plot(snr, g('dep_int'), color=S.GRAY, ls='--', marker='s', ms=3, lw=1.1, label='原解码器（1.443 GMAC / 帧）')
    ax.plot(snr, g('int'), color=S.BLUE, marker='o', ms=2.5, lw=1.2, label='浅层解码器（0.250 GMAC / 帧）')
    ax.set_ylabel('PSNR (dB)')
    ax.set_title('W8A12 整数模型', fontsize=8)
    ax.legend(fontsize=6.5, loc='lower right')
    bx.axhline(0, color=S.GRAY, lw=0.6)
    bx.plot(snr, g('int') - g('dep_int'), color=S.BLUE, marker='o', ms=2.5, lw=1.2, label='W8A12 整数模型')
    bx.plot(snr, g('float') - g('dep_float'), color=S.ORANGE, marker='^', ms=2.5, lw=1.0, ls='--', label='浮点模型')
    bx.set_ylim(-0.3, 0.3)
    bx.set_xlabel('信道 SNR (dB)'); bx.set_ylabel('PSNR 差 (dB)')
    bx.set_title('浅层解码器 − 原解码器', fontsize=8)
    bx.legend(fontsize=6.5, loc='lower right', ncol=2)
    S.finish(ax); S.finish(bx)
    fig.tight_layout(); fig.savefig(OUT / 'psnr_snr_shallow.png', bbox_inches='tight', pad_inches=0.03)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ckpt', required=True)
    ap.add_argument('--cache', required=True)
    ap.add_argument('--sweep', default=str(OUT / 'int_sweep.json'), help='int_sweep.py output (float and W8A12 PSNR vs SNR)')
    ap.add_argument('--manifest', default=str(REPO / 'data' / 'model_shallow' / 'manifest.json'))
    a = ap.parse_args()
    S.apply()
    OUT.mkdir(parents=True, exist_ok=True)
    dev = torch.device('cuda')
    _, enc, dec, _ = Q.load_models(a.ckpt, dev)
    val = torch.from_numpy(np.load(Path(a.cache) / 'val_center256.npy')).permute(0, 3, 1, 2).to(dev)
    fig_interp(enc, dec, val, dev)
    fig_pwl(enc, dec, val, dev)
    fig_psnr(a.sweep)
    print('figures in', OUT)


if __name__ == '__main__':
    main()
