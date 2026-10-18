"""Per-layer quantization error of the shallow decoder (W8A12): the integer graph is fed the float graph's own input
(same received latent), and every named tensor of the integer model (dequantized) is compared with the float one.
Prints per tensor: output scale, calibrated max, SNR of the integer tensor vs float (dB), saturated fraction.

    python quant_layer_err.py --ckpt <run>/best.pt [--ckpt2 <other run>/best.pt] --cache .. --kodak ..
"""
import argparse
import math
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import int_ref as IR                                           # noqa: E402
import quant_shallow as Q                                      # noqa: E402
from deepjsccq_model import iq_to_latent                      # noqa: E402
from ptq_int16 import symbols, to_q10                         # noqa: E402


class FloatCap(Q.FloatB):
    def __init__(self):
        super().__init__(); self.cap = {}

    def rec(self, name, v):
        self.cap[name] = v.detach().double()
        return super().rec(name, v)


@torch.inference_mode()
def run(ckpt, cache, kodak, dev, snr):
    dep, new, dec, ck = Q.load_models(ckpt, dev)
    st = Q.calibrate(new, dec, cache, dev)
    sc = Q.pwl_input_scales(IR.scales_from_stats(st, 1.5, 12, {'sd.igdn': 2.0}), st, 12)
    val, _ = Q.load_images(cache, kodak)
    err, sig, satn, cnt = {}, {}, {}, {}
    gen = torch.Generator(device=dev).manual_seed(777)
    for x in val:
        x = x.to(dev); _, _, h, w = x.shape
        fb = FloatCap(); ib = Q.IntB(sc, st, 32, 32, 8, 12); ib.capture = {}
        idx = IR.encode(Q.FloatB(), new, x)
        nz = torch.randn(idx.shape, generator=gen, device=dev) * math.sqrt(10 ** (-snr / 10) / 2)
        y = to_q10(iq_to_latent(symbols(idx) + nz, 16, h // 4, w // 4))
        Q.shallow_decode(fb, dec, y); Q.shallow_decode(ib, dec, y)
        for k, f in fb.cap.items():
            if k not in ib.capture:
                continue
            qi = ib.capture[k].to(dev).double() * sc[k]
            err[k] = err.get(k, 0.) + float((qi - f).square().sum())
            sig[k] = sig.get(k, 0.) + float(f.square().sum())
            satn[k] = satn.get(k, 0) + int((ib.capture[k].abs() >= 2047).sum()); cnt[k] = cnt.get(k, 0) + f.numel()
    print(f'\n{ckpt}  (DIV2K val, {snr:g} dB, decoder fed the float latent)')
    print(f"{'tensor':16} {'scale':>10} {'cal max':>8} {'SNR dB':>7} {'sat %':>7}")
    for k in err:
        print(f'{k:16} {sc[k]:10.3e} {st[k]:8.3f} {10 * math.log10(sig[k] / max(err[k], 1e-30)):7.1f} '
              f'{100 * satn[k] / cnt[k]:7.3f}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ckpt', nargs='+', required=True)
    ap.add_argument('--cache', required=True)
    ap.add_argument('--kodak', required=True)
    ap.add_argument('--snr', type=float, default=4.0)
    a = ap.parse_args()
    for c in a.ckpt:
        run(c, a.cache, a.kodak, torch.device('cuda'), a.snr)


if __name__ == '__main__':
    main()
