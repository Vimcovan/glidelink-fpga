"""Linearity along straight paths (cf. Yang & Mandt ICCV 2023, Fig. 2-4), 100 random DIV2K validation pairs.
Encoder: x(t) = (1-t) x0 + t x1 -> z(t) = f(x(t)) vs the chord (1-t) z0 + t z1 (continuous latent and after 64-QAM).
Decoder: y(t) = (1-t) y0 + t y1 (noiseless received latents) -> g(y(t)) vs (1-t) g(y0) + t g(y1).
Deviation = ||curve(t) - chord(t)|| / ||end1 - end0||, max over t; length = polyline length / chord length."""
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, 'D:/ClaudePrj/deepjsccq-ofdm-fpga/src/model')
import train_shallow_dec as T  # noqa: E402
from deepjsccq_model import iq_to_latent, latent_to_iq  # noqa: E402

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


def path_stats(curve):
    """curve: (11, ...) tensor along t -> (max relative deviation from the chord, length ratio)"""
    c = curve.flatten(1).double()
    chord = c[0][None] + ts.double()[:, None] * (c[-1] - c[0])[None]
    span = (c[-1] - c[0]).norm()
    dev_ = ((c - chord).norm(dim=1) / span).max().item()
    length = (c[1:] - c[:-1]).norm(dim=1).sum().item() / span.item()
    return dev_, length


@torch.no_grad()
def run():
    res = {}
    for i0, i1 in pairs:
        x0, x1 = val[i0:i0 + 1].float() / 255, val[i1:i1 + 1].float() / 255
        xt = x0 + ts[:, None, None, None] * (x1 - x0)                     # (11, 3, 256, 256)
        for name, m in (('deployed encoder', dep), ('joint encoder (pre4)', jenc)):
            z = m.encoder(xt)
            q = iq_to_latent(latent_to_iq(m.quantizer(z)), *z.shape[1:])
            res.setdefault(f'{name}: continuous latent', []).append(path_stats(z))
            res.setdefault(f'{name}: after 64-QAM', []).append(path_stats(q))
        for name, m, d in (('deployed decoder', dep, dep.decoder), ('shallow decoder (pre4)', jenc, sdec)):
            y = torch.cat([iq_to_latent(latent_to_iq(m.quantizer(m.encoder(x))), 16, 64, 64) for x in (x0, x1)])
            yt = y[0:1] + ts[:, None, None, None] * (y[1:2] - y[0:1])
            res.setdefault(f'{name}: image along latent chord', []).append(path_stats(d(yt)))
        # reference: identity map (perfectly linear) on the image path
        res.setdefault('reference: image path itself', []).append(path_stats(xt))
    print(f'{"":52s} max deviation / span     length / chord')
    print(f'{"":52s}   median   (90th pct)     median  (90th pct)')
    for k, v in res.items():
        a = np.array(v)
        print(f'{k:52s} {np.median(a[:, 0]):7.3f}  ({np.percentile(a[:, 0], 90):6.3f})     '
              f'{np.median(a[:, 1]):6.3f}  ({np.percentile(a[:, 1], 90):6.3f})')


run()
