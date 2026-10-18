"""Linearity of the parts of the shallow decoder along straight paths (as interp_test.py), 100 DIV2K validation pairs,
noiseless received latents of the jointly trained encoder:
  whole decoder : y(t) = (1-t) y0 + t y1          -> output image
  front (64x64) : y(t)                            -> output of the last residual block h
  tail          : h(t) = (1-t) h(y0) + t h(y1)    -> logits before the sigmoid / output image
Deviation = max_t ||curve(t) - chord(t)|| / ||end1 - end0||; length = polyline length / chord length
(a linear map: 0 and 1).

    python interp_stages.py --ckpt <run>/best.pt --cache <data/cache>
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import quant_shallow as Q                                     # noqa: E402
from deepjsccq_model import iq_to_latent                      # noqa: E402
from train_shallow_dec import tx_iq                           # noqa: E402

TS = torch.linspace(0, 1, 11)


def path_stats(curve):
    c = curve.flatten(1).double()
    ts = TS.to(c.device).double()
    chord = c[0][None] + ts[:, None] * (c[-1] - c[0])[None]
    span = (c[-1] - c[0]).norm()
    return ((c - chord).norm(dim=1) / span).max().item(), (c[1:] - c[:-1]).norm(dim=1).sum().item() / span.item()


def front(dec, y):
    h = dec.head(y)
    return dec.pre(h)


def tail(dec, h):
    m = dec.act(dec.conv1(h)) + dec.conv_res(h)
    z = dec.conv2(dec.mid(m))
    return z, torch.sigmoid(z)


@torch.no_grad()
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ckpt', required=True)
    ap.add_argument('--cache', required=True)
    ap.add_argument('--pairs', type=int, default=100)
    a = ap.parse_args()
    dev = torch.device('cuda')
    _, enc, dec, _ = Q.load_models(a.ckpt, dev)
    val = torch.from_numpy(np.load(Path(a.cache) / 'val_center256.npy')).permute(0, 3, 1, 2).to(dev)
    g = torch.Generator().manual_seed(7)
    pairs = [tuple(torch.randperm(len(val), generator=g)[:2].tolist()) for _ in range(a.pairs)]
    ts = TS.to(dev).view(-1, 1, 1, 1)
    res = {k: [] for k in ('whole decoder: latent -> image', 'front: latent -> residual-block output',
                           'tail: residual-block output -> logits', 'tail: residual-block output -> image')}
    for i, j in pairs:
        x = val[[i, j]].float() / 255
        iq, shape = tx_iq(enc, x)
        y = iq_to_latent(iq, *shape)                           # noiseless received latents (2, 16, 64, 64)
        yt = (1 - ts) * y[0:1] + ts * y[1:2]                   # (11, 16, 64, 64)
        res['whole decoder: latent -> image'].append(path_stats(dec(yt)))
        h = front(dec, yt)
        res['front: latent -> residual-block output'].append(path_stats(h))
        ht = (1 - ts) * h[0:1] + ts * h[-1:]
        z, img = tail(dec, ht)
        res['tail: residual-block output -> logits'].append(path_stats(z))
        res['tail: residual-block output -> image'].append(path_stats(img))
    print(f'{len(pairs)} DIV2K validation pairs, median (25-75 %) over pairs')
    print(f"{'part':42s} {'max deviation / span':>24s} {'length / span':>20s}")
    for k, v in res.items():
        d, l = np.array(v).T
        print(f'{k:42s} {np.median(d):8.3f} ({np.percentile(d, 25):.3f}-{np.percentile(d, 75):.3f})'
              f' {np.median(l):9.3f} ({np.percentile(l, 25):.3f}-{np.percentile(l, 75):.3f})')


if __name__ == '__main__':
    main()
