"""Functional test of the polynomial view of the learned PWL activations (B2-PWL, joint): replace every PWL by its
least-squares polynomial (fitted on the inputs it actually sees at 10 dB), no retraining, and measure PSNR.
Variants: degree 1 / 2 / 3, polynomial everywhere, or polynomial inside the 0.5-99.5 % input range with the PWL
(i.e. the learned linear-ish tails) outside."""
import copy
import sys

import numpy as np
import torch
from torch import nn

sys.path.insert(0, 'D:/ClaudePrj/deepjsccq-ofdm-fpga/src/model')
import train_shallow_dec as T  # noqa: E402

dev = 'cuda'
R = 'D:/ClaudePrj/DeepJSCC-Q/runs_shallow/'
model = T.load_encoder(T.REPO / 'data/model/checkpoint/best.pt', dev)
ck = torch.load(R + 'joint_sp_k3_n24_b2pwl/best.pt', map_location=dev, weights_only=False)
dec = T.build(ck['args']).to(dev).eval(); dec.load_state_dict(ck['decoder'])
enc = T.load_encoder(T.REPO / 'data/model/checkpoint/best.pt', dev); enc.encoder.load_state_dict(ck['encoder'])
val = torch.from_numpy(np.load('D:/ClaudePrj/DeepJSCC-Q/data/cache/val_center256.npy')).permute(0, 3, 1, 2).to(dev)

# inputs seen by each PWL (fit set: first 50 validation images at 10 dB; PSNR is measured on all 100)
names = [n for n, m in dec.named_modules() if isinstance(m, T.PWLAct)]
seen = {n: [] for n in names}
hs = [dict(dec.named_modules())[n].register_forward_hook(
    lambda m, i, o, n=n: seen[n].append(i[0].detach().transpose(0, 1).reshape(i[0].shape[1], -1)[:, ::17].cpu()))
    for n in names]
with torch.no_grad(), torch.random.fork_rng(devices=[dev]):
    torch.manual_seed(999)
    for i in range(0, 50, 25):
        dec(T.channel_symbols(enc, val[i:i + 25].float() / 255, 10.0))
for h in hs:
    h.remove()


class PolyAct(nn.Module):
    def __init__(self, pwl, coef, lo, hi, tails):
        super().__init__()
        self.pwl, self.tails = pwl, tails
        self.register_buffer('coef', torch.tensor(coef, dtype=torch.float32))     # (C, deg+1), highest power first
        self.register_buffer('lo', torch.tensor(lo, dtype=torch.float32).view(1, -1, 1, 1))
        self.register_buffer('hi', torch.tensor(hi, dtype=torch.float32).view(1, -1, 1, 1))

    def forward(self, x):
        y = torch.zeros_like(x)
        for p in range(self.coef.shape[1]):
            y = y * x + self.coef[:, p].view(1, -1, 1, 1)
        if self.tails:
            inside = (x >= self.lo) & (x <= self.hi)
            y = torch.where(inside, y, self.pwl(x))
        return y


def variant(deg, tails):
    d = copy.deepcopy(dec)
    mods = dict(d.named_modules())
    for n in names:
        pwl = mods[n]
        X = torch.cat(seen[n], 1)
        with torch.no_grad():
            Y = pwl(X.view(1, X.shape[0], -1, 1).to(dev)).squeeze(-1)[0].cpu()
        coef, lo, hi = [], [], []
        for c in range(X.shape[0]):
            x, y = X[c].numpy().astype(np.float64), Y[c].numpy().astype(np.float64)
            l, h = np.percentile(x, [0.5, 99.5])
            m = (x >= l) & (x <= h) if tails else np.ones_like(x, bool)
            coef.append(np.polyfit(x[m], y[m], deg)); lo.append(l); hi.append(h)
        parent, attr = n.rsplit('.', 1)
        setattr(mods[parent], attr, PolyAct(pwl, np.array(coef), lo, hi, tails).to(dev))
    return d


@torch.no_grad()
def psnrs(d):
    r = T.sweep(model, d, val, [4.0, 10.0, float('inf')], enc=enc)
    return [r[s]['shallow'] for s in (4.0, 10.0, float('inf'))]


base = psnrs(dec)
print(f'{"B2-PWL as trained":46s} 4 dB {base[0]:.2f}   10 dB {base[1]:.2f}   noiseless {base[2]:.2f}')
for tails in (False, True):
    for deg in (1, 2, 3):
        p = psnrs(variant(deg, tails))
        lab = f'degree {deg} polynomial ' + ('inside 0.5-99.5 %, learned tails outside' if tails else 'everywhere')
        print(f'{lab:46s} 4 dB {p[0]:.2f} ({p[0] - base[0]:+.2f})  10 dB {p[1]:.2f} ({p[1] - base[1]:+.2f})  '
              f'noiseless {p[2]:.2f} ({p[2] - base[2]:+.2f})')
