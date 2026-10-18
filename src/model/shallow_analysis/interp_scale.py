"""Is the encoder's curvature along x(t) mostly a gain normalisation? Norm of z(t) along the path, and the deviation
from the chord after the best per-t scale a(t): min_a ||z(t) - a chord(t)|| / span (and the same for the decoder)."""
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, 'D:/ClaudePrj/deepjsccq-ofdm-fpga/src/model')
import train_shallow_dec as T  # noqa: E402
from deepjsccq_model import iq_to_latent, latent_to_iq  # noqa: E402

dev = 'cuda'
dep = T.load_encoder(T.REPO / 'data/model/checkpoint/best.pt', dev)
val = torch.from_numpy(np.load('D:/ClaudePrj/DeepJSCC-Q/data/cache/val_center256.npy')).permute(0, 3, 1, 2).to(dev)
g = torch.Generator().manual_seed(7)
pairs = [tuple(torch.randperm(len(val), generator=g)[:2].tolist()) for _ in range(100)]
ts = torch.linspace(0, 1, 11, device=dev)


def stats(curve):
    c = curve.flatten(1).double()
    chord = c[0][None] + ts.double()[:, None] * (c[-1] - c[0])[None]
    span = (c[-1] - c[0]).norm()
    a = (c * chord).sum(1) / (chord * chord).sum(1)              # best scale per t
    raw = ((c - chord).norm(dim=1) / span).max().item()
    scaled = ((c - a[:, None] * chord).norm(dim=1) / span).max().item()
    rel_norm = (c.norm(dim=1) / c[[0, -1]].norm(dim=1).mean()).cpu().numpy()   # norm along t / endpoint norm
    return raw, scaled, rel_norm, a.cpu().numpy()


enc_r, dec_r = [], []
with torch.no_grad():
    for i0, i1 in pairs:
        x0, x1 = val[i0:i0 + 1].float() / 255, val[i1:i1 + 1].float() / 255
        xt = x0 + ts[:, None, None, None] * (x1 - x0)
        enc_r.append(stats(dep.encoder(xt)))
        y = torch.cat([iq_to_latent(latent_to_iq(dep.quantizer(dep.encoder(x))), 16, 64, 64) for x in (x0, x1)])
        yt = y[0:1] + ts[:, None, None, None] * (y[1:2] - y[0:1])
        dec_r.append(stats(dep.decoder(yt)))
for name, r in (('deployed encoder (continuous latent)', enc_r), ('deployed decoder (image)', dec_r)):
    raw = np.median([x[0] for x in r]); sc = np.median([x[1] for x in r])
    nrm = np.median(np.stack([x[2] for x in r]), 0); a = np.median(np.stack([x[3] for x in r]), 0)
    print(f'{name}: max deviation raw {raw:.3f} -> after best per-t scale {sc:.3f}')
    print(f'   norm along t / endpoint norm (median): ' + ' '.join(f'{v:.2f}' for v in nrm))
    print(f'   best scale a(t) onto the chord (median): ' + ' '.join(f'{v:.2f}' for v in a))
