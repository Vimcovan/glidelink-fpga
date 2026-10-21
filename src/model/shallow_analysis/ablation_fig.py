"""Ablation figures of the shallow-decoder write-up (DIV2K validation, float models; all runs 300 noiseless + 800 joint
epochs at 10 dB):
  ablation.png      PSNR of each one-change variant minus the final shallow decoder vs channel SNR
                    (sweep.json of train_shallow_dec.py -> ablation.json)
  noise_stages.png  signal / noise-induced deviation at every stage, relative to the received latent, for the final
                    decoder and the one without the input linear 3x3 (method of denoise_stages.py -> noise_stages.json)
  block_count.png   number of residual blocks 1..6, and two controls with 1 block's receptive field / 4 blocks' PWL
                    count (A) and 1 block's PWL count / 4 blocks' receptive field (B)  (-> block_count.json)

    python ablation_fig.py --runs <runs_shallow> --cache <data/cache>   (rebuilds both json files, needs CUDA)
    python ablation_fig.py                                              (plots from the committed json files)
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve()
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO / 'report' / 'figures' / 'sim'))
import plotstyle as S                                          # noqa: E402
import matplotlib.pyplot as plt                                # noqa: E402

OUT = REPO / 'report' / 'notes' / 'figures' / 'shallow_decoder'
REF = ('最终解码器', 'joint_pre4pwl_lin1_long')
RUNS = [('去掉入口的线性3×3卷积', 'joint_pre4_pwl_long'), ('PWL换成LeakyReLU', 'abl_leaky_r'),
        ('去掉4个残差块', 'abl_nores_r'), ('去掉IGDN', 'abl_noigdn'), ('冻结原编码器', 'abl_frozen')]
# solid lines only, one colour each: low-SNR role (orange), nonlinear inversion (blues and gray), encoder (ink)
STYLE = {'去掉入口的线性3×3卷积': (S.ORANGE, 'D'), 'PWL换成LeakyReLU': (S.GRAY, '^'),
         '去掉4个残差块': (S.BLUE, 'o'), '去掉IGDN': ('#7fa3c8', 's'), '冻结原编码器': (S.INK, 'v')}
NOISE_RUNS = [REF, RUNS[0]]
BLOCKS = [(1, 'abl_pre1'), (2, 'abl_pre2'), (3, 'abl_pre3'), (4, REF[1]), (5, 'abl_pre5'), (6, 'abl_pre6')]
CONTROLS = [('A', 'abl_nlA', '1个3×3残差块 + 3个1×1残差块'), ('B', 'abl_rfB', '1个9×9残差块')]
NOISE_SNRS = (0.0, 10.0)
STAGES = {'input': '接收潜变量', 'lin0': '线性3×3卷积', 'pre0': '残差块1', 'pre1': '残差块2', 'pre2': '残差块3',
          'pre3': '残差块4', 'upsample1 (igdn + skip)': '第一次上采样', 'upsample2 (logits)': '第二次上采样',
          'output image': '输出图像'}


def collect(runs):
    d = {}
    for name, run in [REF] + RUNS:
        sw = json.load(open(Path(runs) / run / 'sweep.json', encoding='utf-8'))['val_div2k']
        d[name] = {k: round(v['shallow'], 4) for k, v in sw.items() if k != 'Infinity'}
    (OUT / 'ablation.json').write_text(json.dumps({'dataset': 'DIV2K validation, 100 centre crops, float models',
                                                   'psnr_db': d}, ensure_ascii=False, indent=1), encoding='utf-8')


def collect_blocks(runs):
    """sweep of the best checkpoint, mean validation PSNR (10 dB) of the last 20 epochs, MAC/frame"""
    import csv
    d = {}
    for key, run in [(str(n), r) for n, r in BLOCKS] + [(k, r) for k, r, _ in CONTROLS]:
        r = Path(runs) / run
        sw = json.load(open(r / 'sweep.json', encoding='utf-8'))['val_div2k']
        rows = list(csv.DictReader(open(r / 'training.csv')))
        last = [float(x['val_psnr']) for x in rows[-20:]]
        sm = json.load(open(r / 'summary.json', encoding='utf-8'))
        d[key] = {'psnr_db': {k: round(v['shallow'], 4) for k, v in sw.items()}, 'last20_10db': round(sum(last) / len(last), 4),
                  'best_epoch': sm['best_epoch'], 'gmac': round(sm['shallow_gmac'], 4)}
    (OUT / 'block_count.json').write_text(json.dumps({'dataset': 'DIV2K validation, 100 centre crops, float models',
                                                      'runs': d}, ensure_ascii=False, indent=1), encoding='utf-8')


def collect_noise(runs, cache):
    """per stage: 10 log10(clean power (per-channel mean removed) / power of the noise-induced deviation), relative
    to the received latent; same symbols and noise for both decoders (each with its own jointly trained encoder)"""
    import torch
    sys.path.insert(0, str(HERE.parents[1])); sys.path.insert(0, str(HERE.parent))
    import quant_shallow as Q
    from deepjsccq_model import iq_to_latent
    from train_shallow_dec import awgn, tx_iq
    from denoise_stages import stages
    dev = torch.device('cuda')
    val = torch.from_numpy(np.load(Path(cache) / 'val_center256.npy')[:100]).permute(0, 3, 1, 2).to(dev)
    res = {}
    for name, run in NOISE_RUNS:
        _, enc, dec, _ = Q.load_models(str(Path(runs) / run / 'best.pt'), dev)
        res[name] = {}
        for snr in NOISE_SNRS:
            torch.manual_seed(1234)
            acc_s, acc_d = {}, {}
            with torch.no_grad():
                for i in range(0, len(val), 20):
                    x = val[i:i + 20].float() / 255
                    iq, shape = tx_iq(enc, x)
                    yc, yn = iq_to_latent(iq, *shape), iq_to_latent(awgn(iq, snr), *shape)
                    zc, zn = stages(dec, yc), stages(dec, yn)
                    for k in zc:
                        z0 = zc[k].double() - zc[k].double().mean(dim=(0, 2, 3), keepdim=True)
                        acc_s[k] = acc_s.get(k, 0.0) + float((z0 ** 2).sum())
                        acc_d[k] = acc_d.get(k, 0.0) + float(((zn[k] - zc[k]).double() ** 2).sum())
            r = {k: 10 * math.log10(acc_s[k] / acc_d[k]) for k in acc_s}
            res[name][f'{snr:g}'] = {k: round(r[k] - r['input'], 3) for k in r}
    (OUT / 'noise_stages.json').write_text(json.dumps({'dataset': 'DIV2K validation, 100 centre crops, float models',
                                                       'gain_db_vs_input': res}, ensure_ascii=False, indent=1),
                                           encoding='utf-8')


def plot():
    d = json.load(open(OUT / 'ablation.json', encoding='utf-8'))['psnr_db']
    snr = np.array([float(k) for k in d[REF[0]]])
    ref = np.array(list(d[REF[0]].values()))
    fig, ax = plt.subplots(figsize=(4.6, 3.3), dpi=300)
    ax.axhline(0, color=S.GRAY, lw=0.6)
    for name, _ in RUNS:
        c, m = STYLE[name]
        v = np.array(list(d[name].values())) - ref
        ax.plot(snr, v, color=c, marker=m, ms=3, lw=1.3, label=name)
    ax.set_xlabel('信道 SNR (dB)'); ax.set_ylabel('PSNR 差 (dB)')
    ax.set_title('各改动 − 最终解码器', fontsize=8)
    ax.legend(fontsize=6.3, loc='upper center', bbox_to_anchor=(0.5, -0.2), ncol=3, columnspacing=1.0)
    S.finish(ax)
    fig.tight_layout(); fig.savefig(OUT / 'ablation.png', bbox_inches='tight', pad_inches=0.03)


def plot_noise():
    d = json.load(open(OUT / 'noise_stages.json', encoding='utf-8'))['gain_db_vs_input']
    ks = list(STAGES)
    cols = {(REF[0], '0'): S.BLUE, (REF[0], '10'): '#7fa3c8', (RUNS[0][0], '0'): S.ORANGE, (RUNS[0][0], '10'): '#e8a57a'}
    marks = {'0': 'o', '10': 's'}
    fig = plt.figure(figsize=(7.0, 5.0), dpi=300)
    gs = fig.add_gridspec(2, 3, height_ratios=[1.15, 1], width_ratios=[1.6, 1, 1])
    ax = fig.add_subplot(gs[0, :]); bx = fig.add_subplot(gs[1, 0])
    cx = {'0': fig.add_subplot(gs[1, 1]), '10': fig.add_subplot(gs[1, 2])}
    tail = [7, 8]                                               # second upsampling, output image
    for name in d:
        for snr in d[name]:
            x = [i for i, k in enumerate(ks) if k in d[name][snr]]
            v = [d[name][snr][ks[i]] for i in x]
            st = dict(color=cols[(name, snr)], marker=marks[snr], ms=3, lw=1.3)
            lab = f"{'最终解码器' if name == REF[0] else '去掉线性3×3卷积'}，{snr} dB"
            ax.plot(x, v, label=lab, **st)
            bx.plot(x[:-3], v[:-3], **st)
            cx[snr].plot(range(len(tail)), [d[name][snr][ks[i]] for i in tail], **st)
            yv = d[name][snr][ks[8]]
            cx[snr].annotate(f'{yv:.2f}', (1, yv), xytext=(5, 0), textcoords='offset points', va='center',
                             fontsize=6, color=S.INK)
    ax.axvspan(-0.3, 5.3, color=S.SHADE_B, lw=0)
    ax.text(2.5, 3.6, '64×64', ha='center', fontsize=7, color=S.INK)
    ax.set_xticks(range(len(ks))); ax.set_xticklabels([STAGES[k] for k in ks], fontsize=6.3, rotation=20, ha='right')
    ax.set_ylabel('SNR提升 (dB)', fontsize=7)
    bx.set_ylabel('SNR提升 (dB)', fontsize=7)
    fig.suptitle('各级输出SNR相对接收潜变量SNR的提升', fontsize=8.5)
    ax.set_title('(a) 各级', fontsize=8)
    ax.legend(fontsize=6.3, loc='upper left')
    bx.set_xticks(range(6)); bx.set_xticklabels([STAGES[k] for k in ks[:6]], fontsize=6.3, rotation=30, ha='right')
    bx.set_title('(b) 64×64上的各级', fontsize=8)
    for snr, c in cx.items():
        c.set_xticks(range(len(tail))); c.set_xticklabels([STAGES[ks[i]] for i in tail], fontsize=6.3, rotation=30, ha='right')
        c.set_xlim(-0.3, 1.5)
        c.set_title(f'({"c" if snr == "0" else "d"}) 输出端，{snr} dB信道', fontsize=8)
    for a in (ax, bx, *cx.values()):
        S.finish(a)
    fig.tight_layout(); fig.savefig(OUT / 'noise_stages.png', bbox_inches='tight', pad_inches=0.03)


def plot_blocks():
    d = json.load(open(OUT / 'block_count.json', encoding='utf-8'))['runs']
    ns = [n for n, _ in BLOCKS]
    fig, ax = plt.subplots(figsize=(4.6, 3.0), dpi=300)
    ax.axhline(0, color=S.GRAY, lw=0.6)
    for snr, c, m, lab in (('4', S.ORANGE, 'D', '4 dB'), ('10', S.BLUE, 'o', '10 dB'), ('20', '#7fa3c8', 's', '20 dB'),
                           ('Infinity', S.INK, 'v', '无噪声')):
        ref = d['4']['psnr_db'][snr]
        ax.plot(ns, [d[str(n)]['psnr_db'][snr] - ref for n in ns], color=c, marker=m, ms=3, lw=1.3, label=lab)
    ax.set_xticks(ns)
    ax.set_xticklabels([f"{n}\n{d[str(n)]['gmac']:.3f} G" for n in ns], fontsize=6.5)
    ax.set_xlabel('残差块个数（下：每帧乘累加量）'); ax.set_ylabel('PSNR 差 (dB)')
    ax.set_title('与4个残差块的PSNR差', fontsize=8)
    ax.legend(fontsize=6.5, loc='lower right')
    S.finish(ax)
    fig.tight_layout(); fig.savefig(OUT / 'block_count.png', bbox_inches='tight', pad_inches=0.03)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', default=None, help='runs directory with the training outputs (rebuilds the json files)')
    ap.add_argument('--cache', default=None, help='data/cache (with --runs: stage noise measurement)')
    a = ap.parse_args()
    S.apply()
    if a.runs:
        collect(a.runs)
        collect_blocks(a.runs)
        if a.cache:
            collect_noise(a.runs, a.cache)
    plot()
    plot_noise()
    plot_blocks()


if __name__ == '__main__':
    main()
