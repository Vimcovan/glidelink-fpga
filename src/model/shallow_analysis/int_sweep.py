"""PSNR vs channel SNR of the W8A12 integer models (shallow encoder + decoder, and the deployed model) on the DIV2K
validation crops, same noise for both (quant_shallow.evaluate), plus the float values of the same runs.

    python int_sweep.py --ckpt data/model_shallow/checkpoint/best.pt --cache <data/cache> --kodak <data/Kodak>
        --out report/notes/figures/shallow_decoder/int_sweep.json
"""
import argparse
import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import int_ref as IR                                           # noqa: E402
import quant_shallow as Q                                      # noqa: E402

SNRS = [0, 2, 4, 6, 8, 10, 12, 15, 20, 25, 30]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ckpt', required=True)
    ap.add_argument('--cache', required=True)
    ap.add_argument('--kodak', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--snr', type=float, nargs='*', default=SNRS)
    a = ap.parse_args()
    torch.backends.cudnn.allow_tf32 = False; torch.backends.cuda.matmul.allow_tf32 = False
    dev = torch.device('cuda')
    dep, enc, dec, _ = Q.load_models(a.ckpt, dev)
    Q.IntB.PWL_FORM = 'knot'
    st = Q.calibrate(enc, dec, a.cache, dev)
    sc = Q.pwl_input_scales(IR.scales_from_stats(st, 1.5, 12, {'sd.igdn': 2.0}), st, 12)
    st_dep = Q.calibrate_deployed(dep, a.cache, dev)
    sc_dep = IR.scales_from_stats(st_dep, 1.5, 12, {'dec.9.igdn': 2.0})
    val, _ = Q.load_images(a.cache, a.kodak)
    res = {}
    for snr in a.snr:
        r, _ = Q.evaluate(val, snr, dev, enc, dec, sc, st, dep, sc_dep, st_dep, 8, 12)
        res[str(snr)] = {k: round(v, 4) for k, v in r.items()}
        print(f"{snr:5g} dB: shallow float {r['float']:.2f} int {r['int']:.2f} | deployed float {r['dep_float']:.2f} "
              f"int {r['dep_int']:.2f} | int diff {r['int'] - r['dep_int']:+.2f}", flush=True)
    Path(a.out).write_text(json.dumps({'dataset': 'DIV2K validation, 100 centre crops', 'psnr_db': res}, indent=1),
                           encoding='utf-8')
    print('written', a.out)


if __name__ == '__main__':
    main()
