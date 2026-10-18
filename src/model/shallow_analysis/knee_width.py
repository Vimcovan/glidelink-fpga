"""Knee width of the learned PWL activations: noiseless-trained (frozen encoder) vs joint at 10 dB.
For each channel: left slope sL (mean slope on [-2, -1]), right slope sR (mean on [1, 2]); the slope curve f'(x) moves
from sL to sR; knee width = distance between the x where f' has covered 10 % and 90 % of the way.
Also: residual after fitting the best softplus-type curve a x + b + c softplus((x - x0) / w) * w (affine part free)."""
import sys

import numpy as np
import torch

sys.path.insert(0, 'D:/ClaudePrj/deepjsccq-ofdm-fpga/src/model')
import train_shallow_dec as T  # noqa: E402

xs = torch.linspace(-3, 3, 1201)
x = xs.numpy()


def curves(path):
    ck = torch.load(path, map_location='cpu', weights_only=False)
    d = T.build(ck['args']); d.load_state_dict(ck['decoder'])
    out = []
    for a in [m for m in d.modules() if isinstance(m, T.PWLAct)]:
        with torch.no_grad():
            out.append(a(xs.view(1, 1, -1, 1).repeat(1, a.a.shape[1], 1, 1)).squeeze(-1)[0].numpy())
    return np.concatenate(out)                                 # (64, 1201)


def knee(y):
    dy = np.gradient(y, x, axis=1)
    sL = dy[:, (x >= -2) & (x <= -1)].mean(1); sR = dy[:, (x >= 1) & (x <= 2)].mean(1)
    frac = (dy - sL[:, None]) / (sR - sL)[:, None]
    widths, centres = [], []
    for f in frac:
        sm = np.convolve(f, np.ones(21) / 21, mode='same')     # the PWL slope is a staircase: smooth a little
        i10 = np.argmax(sm >= 0.1); i90 = np.argmax(sm >= 0.9)
        widths.append(x[i90] - x[i10]); centres.append(0.5 * (x[i90] + x[i10]))
    return sL, sR, np.array(widths), np.array(centres)


R = 'D:/ClaudePrj/DeepJSCC-Q/runs_shallow/'
for name in ('clean_sp_k3_n24_b2pwl', 'joint_sp_k3_n24_b2pwl'):
    y = curves(R + name + '/best.pt')
    sL, sR, w, c = knee(y)
    mono = (sR > sL)
    print(f'{name:26s}: left slope median {np.median(sL):+.2f}, right {np.median(sR):+.2f}; '
          f'knee width (10-90 %) median {np.median(w[mono]):.2f} (IQR {np.percentile(w[mono], 25):.2f}-{np.percentile(w[mono], 75):.2f}), '
          f'knee centre median {np.median(c[mono]):+.2f}; channels with right > left slope {mono.sum()}/64')
