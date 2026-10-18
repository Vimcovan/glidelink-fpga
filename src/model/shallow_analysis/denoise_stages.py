"""How much of the channel noise each stage of the shallow decoder removes.

The same received symbols are decoded twice: clean (64-QAM symbols as sent) and noisy (+ AWGN). For every stage
output z, the noise-induced deviation d = z(noisy) - z(clean) is compared with the clean output:
    SNR_stage = ||z(clean) - mean||^2 / ||d||^2   (per-channel mean removed, all channels and pixels pooled)
For the linear 3x3 at the input this is exactly the SNR after the filter (d = L(n), the bias cancels), and the gain
over the input SNR ||s||^2 / ||n||^2 is its noise suppression. The later stages are nonlinear: there the ratio is
the size of the noise-induced deviation relative to the clean signal at that point (not a filter gain). Also prints
the effective noise gain of the linear filter (output noise power per unit input noise, white input) and the
part of the clean signal it keeps.

    python denoise_stages.py --ckpt <run>/best.pt --cache <data/cache> [--snr 4 10 20]
"""
import argparse
import math
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import quant_shallow as Q                                     # noqa: E402
from deepjsccq_model import iq_to_latent                      # noqa: E402
from train_shallow_dec import awgn, tx_iq                     # noqa: E402


def stages(dec, y):
    """outputs of the stages of the shallow decoder (float), keyed by name"""
    out = {'input': y}
    h = y
    for i, m in enumerate(dec.head):
        h = m(h); out[f'lin{i}'] = h
    for i, b in enumerate(dec.pre):
        h = b(h); out[f'pre{i}'] = h
    m = dec.act(dec.conv1(h)); r = dec.conv_res(h)
    out['upsample1 (igdn + skip)'] = m + r
    out['upsample2 (logits)'] = dec.conv2(dec.mid(m + r))
    out['output image'] = torch.sigmoid(out['upsample2 (logits)'])
    return out


@torch.no_grad()
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ckpt', required=True)
    ap.add_argument('--cache', required=True)
    ap.add_argument('--snr', type=float, nargs='*', default=[4.0, 10.0, 20.0])
    ap.add_argument('--n', type=int, default=100)
    a = ap.parse_args()
    dev = torch.device('cuda')
    _, enc, dec, _ = Q.load_models(a.ckpt, dev)
    val = torch.from_numpy(np.load(Path(a.cache) / 'val_center256.npy')[:a.n]).permute(0, 3, 1, 2).to(dev)
    if len(dec.head):
        w = dec.head[0].weight.double()                        # (16, 16, 3, 3)
        ng = (w ** 2).sum(dim=(1, 2, 3))                       # white-noise power gain per output channel
        print(f'linear 3x3: white-noise power gain per output channel {ng.mean():.3f} (min {ng.min():.3f}, '
              f'max {ng.max():.3f}); centre-tap share of the weight energy '
              f'{float((w[:, :, 1, 1] ** 2).sum() / (w ** 2).sum()):.2f}')
    for snr in a.snr:
        torch.manual_seed(1234)
        acc_s, acc_d, sin, nin = {}, {}, 0.0, 0.0
        for i in range(0, len(val), 20):
            x = val[i:i + 20].float() / 255
            iq, shape = tx_iq(enc, x)
            noisy = awgn(iq, snr)
            yc, yn = iq_to_latent(iq, *shape), iq_to_latent(noisy, *shape)
            sin += float((yc ** 2).sum()); nin += float(((yn - yc) ** 2).sum())
            zc, zn = stages(dec, yc), stages(dec, yn)
            for k in zc:
                zc0 = zc[k].double() - zc[k].double().mean(dim=(0, 2, 3), keepdim=True)   # per-channel mean removed
                acc_s[k] = acc_s.get(k, 0.0) + float((zc0 ** 2).sum())
                acc_d[k] = acc_d.get(k, 0.0) + float(((zn[k] - zc[k]).double() ** 2).sum())
        print(f'\nchannel SNR {snr:g} dB (measured input SNR {10 * math.log10(sin / nin):.2f} dB)')
        print(f"{'stage':26s} {'signal/deviation dB':>20s} {'vs input':>9s}")
        base = 10 * math.log10(acc_s['input'] / acc_d['input'])
        for k in acc_s:
            r = 10 * math.log10(acc_s[k] / acc_d[k])
            print(f'{k:26s} {r:20.2f} {r - base:+9.2f}')


if __name__ == '__main__':
    main()
