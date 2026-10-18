"""Where does the shallow decoder (joint pre4) lose against the deployed one? Same images, same channel noise.
Error spectrum by band, reconstruction vs original spectrum (blur vs added artifacts), error by position in the
4x4 upsampling grid (PixelShuffle pattern), gradient energy (sharpness), example crops."""
import math
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

sys.path.insert(0, 'D:/ClaudePrj/deepjsccq-ofdm-fpga/src/model')
import train_shallow_dec as T  # noqa: E402

dev = 'cuda'
R = Path('D:/ClaudePrj/DeepJSCC-Q/runs_shallow')
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
model = T.load_encoder(T.REPO / 'data/model/checkpoint/best.pt', dev)
ck = torch.load(R / sys.argv[2] / 'best.pt', map_location=dev, weights_only=False)
dec = T.build({**ck['args']}).to(dev).eval(); dec.load_state_dict(ck['decoder'])
enc = T.load_encoder(T.REPO / 'data/model/checkpoint/best.pt', dev); enc.encoder.load_state_dict(ck['encoder'])
val = torch.from_numpy(np.load('D:/ClaudePrj/DeepJSCC-Q/data/cache/val_center256.npy')).permute(0, 3, 1, 2).to(dev)

N = 256
fy = np.fft.fftfreq(N)[:, None]; fx = np.fft.fftfreq(N)[None, :]
rad = np.sqrt(fy ** 2 + fx ** 2)
BANDS = [(0, 1 / 32), (1 / 32, 1 / 16), (1 / 16, 1 / 8), (1 / 8, 1 / 4), (1 / 4, 0.71)]
BNAME = ['<1/32', '1/32-1/16', '1/16-1/8', '1/8-1/4', '>1/4 cyc/px']


def spec(a):  # (B,3,H,W) -> mean power spectrum over images and channels
    return (np.abs(np.fft.fft2(a.cpu().numpy())) ** 2).mean(axis=(0, 1))


def bands(p):
    return np.array([p[(rad >= lo) & (rad < hi)].sum() for lo, hi in BANDS])


def grad_energy(a):
    a = a.float()
    return float(((a[..., 1:, :] - a[..., :-1, :]) ** 2).mean() + ((a[..., :, 1:] - a[..., :, :-1]) ** 2).mean())


for snr in [10.0, float('inf')]:
    acc = {k: np.zeros((N, N)) for k in ('x', 'old', 'new', 'eold', 'enew')}
    phase = {k: np.zeros((4, 4)) for k in ('old', 'new')}
    g = {k: 0. for k in ('x', 'old', 'new')}; nb = 0; keep = None
    with torch.no_grad(), torch.random.fork_rng(devices=[dev]):
        torch.manual_seed(4321)
        for i in range(0, len(val), 25):
            x = val[i:i + 25].float().div(255)
            y_old, y_new = T.pair_symbols(model, enc, x, snr)
            ro, rn = model.decoder(y_old), dec(y_new)
            for k, a in (('x', x), ('old', ro), ('new', rn), ('eold', ro - x), ('enew', rn - x)):
                acc[k] += spec(a)
            for k, r in (('old', ro), ('new', rn)):
                e2 = ((r - x) ** 2).mean(1)[0:].cpu().numpy()  # (B,H,W)
                phase[k] += e2.reshape(-1, N // 4, 4, N // 4, 4).mean(axis=(0, 1, 3))
            for k, a in (('x', x), ('old', ro), ('new', rn)):
                g[k] += grad_energy(a)
            nb += 1
            if keep is None:
                keep = (x[:6].cpu(), ro[:6].cpu(), rn[:6].cpu())
    tag = '10dB' if snr == 10 else 'noiseless'
    bo, bn, bx = bands(acc['eold']), bands(acc['enew']), bands(acc['x'])
    tot_o, tot_n = bo.sum(), bn.sum()
    print(f'\n=== {tag} (DIV2K validation, 100 images)')
    print('error energy by spatial-frequency band (share of total error; shallow/deployed ratio):')
    for name, a, b in zip(BNAME, bo, bn):
        print(f'  {name:12s} deployed {a / tot_o * 100:5.1f} %   shallow {b / tot_n * 100:5.1f} %   ratio {b / a:5.2f}')
    print(f'  total error ratio shallow/deployed {tot_n / tot_o:.2f}  (= {10*math.log10(tot_n / tot_o):.2f} dB)')
    so, sn = bands(acc['old']), bands(acc['new'])
    print('reconstruction power / original power per band (1 = same; <1 = smoothed, >1 = added energy):')
    for name, a, b, c in zip(BNAME, so, sn, bx):
        print(f'  {name:12s} deployed {a / c:5.3f}   shallow {b / c:5.3f}')
    print(f'gradient energy / original: deployed {g["old"] / g["x"]:.3f}  shallow {g["new"] / g["x"]:.3f}')
    for k in ('old', 'new'):
        p = phase[k] / nb
        print(f'error by 4x4 grid position ({ "deployed" if k == "old" else "shallow" }): max/min {p.max() / p.min():.3f}')
        print('   ' + '\n   '.join(' '.join(f'{v * 1e4:6.2f}' for v in row) for row in p))
    # peaks of the shallow error spectrum at the PixelShuffle frequencies (1/4, 1/2 cyc/px) vs neighbourhood
    en = acc['enew'] / nb; eo = acc['eold'] / nb
    for f in (64, 128):   # 1/4 and 1/2 cycles per pixel
        for nm, e in (('deployed', eo), ('shallow', en)):
            pk = e[0, f] + e[f, 0]
            nbh = (e[0, f - 3:f - 1].mean() + e[0, f + 2:f + 4].mean() + e[f - 3:f - 1, 0].mean() + e[f + 2:f + 4, 0].mean()) / 2
            print(f'  error spectrum at {f / N:.2f} cyc/px ({nm}): peak / neighbours {pk / max(nbh, 1e-12):.1f}')
    # crops: original | deployed | shallow | |error| x4 (deployed, shallow)
    x0, o0, n0 = keep
    rows = []
    for j in range(6):
        cy, cx = 64, 64
        crop = lambda a: a[j, :, cy:cy + 128, cx:cx + 128].permute(1, 2, 0).clamp(0, 1).numpy()
        err = lambda a: np.repeat((np.abs(crop(a) - crop(x0)).mean(2, keepdims=True) * 4).clip(0, 1), 3, 2)
        rows.append(np.concatenate([crop(x0), crop(o0), crop(n0), err(o0), err(n0)], 1))
    img = (np.concatenate(rows, 0) * 255).round().astype(np.uint8)
    Image.fromarray(img).resize((img.shape[1] * 2, img.shape[0] * 2), Image.NEAREST).save(OUT / f'crops_{tag}.png')
    print(f'crops -> {OUT / f"crops_{tag}.png"}  (columns: original | deployed | shallow | |err|x4 deployed | |err|x4 shallow)')
