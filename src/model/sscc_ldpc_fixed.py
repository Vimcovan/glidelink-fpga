"""Fixed-point layered LDPC decoder of the RTL against the floating-point reference: codeword error rate of the
802.11n code (n = 1944) on 64-QAM / AWGN, per SNR, for decoder configurations
    ref        exact LLR, flooding normalized min-sum, 40 iterations (the decoder of sscc_sim.py)
    fl<I>      exact LLR, layered, at most I iterations (floating point)
    q<b>s<s>a<A>m<M>i<I>   RTL demapper LLRs with magnitude <= 2^(b-1)-1 after >> s, APP A bits, v2c M bits,
               layered, at most I iterations
A frame (101 codewords) is error-free with probability ~ (1 - CER)^101, so the cliff of the image is where CER
reaches ~1e-4.

    python sscc_ldpc_fixed.py --out <json> [--rate 2/3] [--snr 13:16.01:0.25] [--ncw 20000] --cfg ref,fl8,q6s4a8m6i8
"""
import argparse
import json
import math
import re
from pathlib import Path

import numpy as np
import torch

import sscc_sim as S


def run(code, cfg, snr, ncw, seed, chunk=5000):
    err = 0; it_sum = 0.0; it_max = 0
    g = torch.Generator(device=S.dev).manual_seed(seed)
    for c0 in range(0, ncw, chunk):
        B = min(chunk, ncw - c0)
        info = torch.randint(0, 2, (B, code.k), generator=g, device=S.dev).float()
        cw = code.encode(info)
        x = S.modulate(cw)                                     # n = 1944 = 324 symbols per codeword
        nz = (torch.randn(x.shape, generator=g, device=S.dev) + 1j * torch.randn(x.shape, generator=g, device=S.dev))
        y = x + (nz * math.sqrt(10 ** (-snr / 10) / 2)).to(x.dtype)
        if cfg == 'ref':
            hb, ok = code.decode(S.llr(y, snr)); used = torch.full((B,), S.ITER, device=S.dev)
        elif cfg.startswith('fl'):
            hb, ok, used = code.decode_layered(S.llr(y, snr), int(cfg[2:]))
        else:
            b, s, a, m, i = (int(v) for v in re.fullmatch(r'q(\d+)s(\d+)a(\d+)m(\d+)i(\d+)', cfg).groups())
            hb, ok, used = code.decode_layered(S.llr_rtl(y, s, 2 ** (b - 1) - 1), i, a, m)
        err += int((hb[:, :code.k] != info).any(1).sum())
        it_sum += float(used.sum()); it_max = max(it_max, int(used.max()))
    return dict(cer=err / ncw, errors=err, iter_mean=round(it_sum / ncw, 3), iter_max=it_max)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--vivado', default='D:/AMDDesignTools/2025.2/Vivado')
    ap.add_argument('--rate', default='2/3')
    ap.add_argument('--snr', default='13:16.01:0.25')
    ap.add_argument('--ncw', type=int, default=20000)
    ap.add_argument('--cfg', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    code = S.LDPC(a.vivado, a.rate)
    lo, hi, st = (float(v) for v in a.snr.split(':'))
    out = json.load(open(a.out, encoding='utf-8')) if Path(a.out).exists() else {}
    for cfg in a.cfg.split(','):
        r = out.setdefault(f'{a.rate} {cfg}', {})
        for k, snr in enumerate(np.arange(lo, hi, st)):
            r[f'{snr:g}'] = run(code, cfg, float(snr), a.ncw, seed=1000 + k)
            print(f'{a.rate} {cfg:16s} {snr:6.2f} dB  CER {r[f"{snr:g}"]["cer"]:.2e}  '
                  f'iter mean {r[f"{snr:g}"]["iter_mean"]:.2f} max {r[f"{snr:g}"]["iter_max"]}', flush=True)
            Path(a.out).write_text(json.dumps(out, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
