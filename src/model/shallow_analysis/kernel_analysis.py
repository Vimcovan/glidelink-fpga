"""A (pre4 + 7x7 first layer, joint): what do the 7x7 kernels use? spatial extent, channel low rank, separability,
and PSNR after truncating the kernel / replacing it with a low-rank one (no retraining)."""
import copy
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, 'D:/ClaudePrj/deepjsccq-ofdm-fpga/src/model')
import train_shallow_dec as T  # noqa: E402

dev = 'cuda'
R = Path('D:/ClaudePrj/DeepJSCC-Q/runs_shallow')
model = T.load_encoder(T.REPO / 'data/model/checkpoint/best.pt', dev)
ck = torch.load(R / 'joint_sp_k7_n24_pre4/best.pt', map_location=dev, weights_only=False)
dec = T.build({**ck['args']}).to(dev).eval(); dec.load_state_dict(ck['decoder'])
enc = T.load_encoder(T.REPO / 'data/model/checkpoint/best.pt', dev); enc.encoder.load_state_dict(ck['encoder'])
val = torch.from_numpy(np.load('D:/ClaudePrj/DeepJSCC-Q/data/cache/val_center256.npy')).permute(0, 3, 1, 2).to(dev)

yy, xx = np.mgrid[-3:4, -3:4]; cheb = np.maximum(abs(yy), abs(xx))   # 0 centre, 1: 3x3, 2: 5x5 ring, 3: 7x7 ring


def describe(name, w):
    w = w.detach().cpu().numpy()                     # (96, 16, 7, 7)
    e = (w ** 2).sum(axis=(0, 1))                    # energy per tap
    tot = e.sum()
    print(f'\n--- {name}: weight {w.shape}')
    print('  energy share  centre 3x3 {:.1f} %  | 5x5 ring {:.1f} %  | outer 7x7 ring {:.1f} %'.format(
        e[cheb <= 1].sum() / tot * 100, e[cheb == 2].sum() / tot * 100, e[cheb == 3].sum() / tot * 100))
    print('  per-tap energy (% of total), 7x7:')
    for row in e / tot * 100:
        print('    ' + ' '.join(f'{v:5.2f}' for v in row))
    # channel low rank: W[o, i, :, :] = sum_r A[o, r] B[r, i, :, :]  <->  SVD of (96, 16*49)
    s = np.linalg.svd(w.reshape(96, -1), compute_uv=False); c = np.cumsum(s ** 2) / (s ** 2).sum()
    print('  channel low rank (16->r kxk conv, then 1x1 r->96): energy kept r=4 {:.1f} %, r=8 {:.1f} %, r=12 {:.1f} %, '
          'r=16 {:.1f} %;  r for 90/95/99 %: {}/{}/{}'.format(c[3] * 100, c[7] * 100, c[11] * 100, c[15] * 100,
                                                             *(int(np.searchsorted(c, q) + 1) for q in (.9, .95, .99))))
    # input-side low rank: W[o, i] = sum_r A[o, i, r] ... -> SVD of (96*16... ) skip; spatial separability per slice
    sl = w.reshape(-1, 7, 7); r1 = []
    for k in sl:
        sv = np.linalg.svd(k, compute_uv=False); r1.append(sv[0] ** 2 / max((sv ** 2).sum(), 1e-20))
    r1 = np.array(r1); wgt = (sl ** 2).sum(axis=(1, 2))
    print(f'  spatial separability (7x1 * 1x7 = rank-1 slice): energy in rank 1, weighted mean {np.average(r1, weights=wgt) * 100:.1f} %')


for name in ('conv1', 'conv_res'):
    describe(name, getattr(dec, name).conv.weight)


@torch.no_grad()
def psnr_of(d):
    r = T.sweep(model, d, val, [10.0, float('inf')], enc=enc)
    return r[10.0]['shallow'], r[float('inf')]['shallow']


base = psnr_of(dec)
print(f'\nPSNR (DIV2K val) 10 dB / noiseless: trained {base[0]:.2f} / {base[1]:.2f}')


def variant(fn, label):
    d = copy.deepcopy(dec)
    for name in ('conv1', 'conv_res'):
        conv = getattr(d, name).conv
        conv.weight.data = fn(conv.weight.data)
    p = psnr_of(d)
    print(f'  {label:42s} {p[0]:.2f} / {p[1]:.2f}   ({p[0] - base[0]:+.2f} / {p[1] - base[1]:+.2f})')


mask = lambda m: (lambda w: w * torch.tensor(m, dtype=w.dtype, device=w.device))
variant(mask(cheb <= 2), 'keep 5x5 (outer ring zeroed)')
variant(mask(cheb <= 1), 'keep 3x3')


def lowrank(r):
    def f(w):
        m = w.reshape(96, -1)
        u, s, vt = torch.linalg.svd(m, full_matrices=False)
        return (u[:, :r] * s[:r] @ vt[:r]).reshape(w.shape)
    return f


for r in (4, 8, 12, 16):
    variant(lowrank(r), f'channel low rank r={r} (no retraining)')
