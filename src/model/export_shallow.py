"""Export the jointly trained shallow decoder and its encoder (W8A12) for RTL, in the layout of export_fpga.py:
manifest.json (ops, tensors, memory plan, file index), params/*.mem, golden/<image>/*.mem.

The encoder keeps the deployed structure (int_ref.encode, deployed memory plan); the decoder is the graph of
quant_shallow.shallow_decode: received symbols -> linear 3x3 -> 4 residual blocks with PWL activations -> sub-pixel
3x3 conv (main + linear skip, merged into one engine) -> PixelShuffle 2 -> IGDN (main) -> add -> sub-pixel 3x3 conv
-> PixelShuffle 2 -> sigmoid -> 8-bit RGB.

PWL op (knot form): per channel c and segment s = clamp(((x - T0) >>> STEP_LOG2) + 1, 0, NK), knot k = max(s - 1, 0):
    acc = Y[c][k] + S[c][s] * (x - (T0 + (k << STEP_LOG2)))
    y   = sat12(rsr(sat27(rsr(acc, pre[c])) * M[c], sh[c]))
  params/<op>.slope.mem  [C][NK+1] 8-bit signed;  <op>.yk.mem [C][NK] signed (accumulator scale);
  <op>.rq_pre/rq_m/rq_sh.mem per channel.

    python export_shallow.py --ckpt <run>/best.pt --cache <data/cache> --kodak <data/Kodak> --out-dir data/model_shallow
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

import int_ref as IR
import quant_shallow as Q
from deepjsccq_model import iq_to_latent
from export_fpga import NOISE_SEED, bits_needed, export_params, nhwc, rel_files, tensor_format, write_mem
from ptq_int16 import symbols, to_q10

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'src' / 'network'))
from memory_plan import Planner  # noqa: E402


# ---------------------------------------------------------------------- memory plan of the shallow decoder
class ShallowPlanner(Planner):
    """deployed encoder plan; decoder plan for the shallow graph (names as in quant_shallow.shallow_decode)"""

    def __init__(self, dec, *a):
        super().__init__(*a)
        self.dec = dec

    def plan(self, part):
        if part == 'encoder':
            return super().plan(part)
        E, B = [], []
        sizes_f = REPO / 'src' / 'network' / 'fifo_sizes.json'
        sizes = json.loads(sizes_f.read_text(encoding='utf-8')) if sizes_f.exists() else {}

        def buf(name, kind, elems, fifo=None):
            if fifo in sizes:
                elems, kind = sizes[fifo], kind.split(' (')[0] + ' (measured)'
            B.append({'name': name, 'kind': kind, 'elems': int(elems), 'bits': self.ab})

        c, h, w = 16, 64, 64
        n = self.dec.conv1.conv.out_channels // 4                  # channels after PixelShuffle 2
        for i in range(len(self.dec.head)):
            E.append(self.engine(f'sd.lin{i}', c, c, 3, h, w, 'linear 3x3'))
            buf(f'sd.lin{i} line buffer', '3x3 line buffer (3 rows)', 3 * w * c)
        for i in range(len(self.dec.pre)):
            for j in (1, 2):
                E.append(self.engine(f'sd.pre{i}.c{j}', c, c, 3, h, w))
                buf(f'sd.pre{i}.c{j} line buffer', '3x3 line buffer (3 rows)', 3 * w * c)
            buf(f'sd.pre{i}.skip delay', 'block identity delay FIFO (2 rows)', 2 * w * c, f'sd.pre{i}.out.b')
        E.append(self.engine('sd.conv1_plus_res', c, 2 * 4 * n, 3, h, w, 'upsampling main and skip 3x3 merged (same input)',
                             ['sd.conv1', 'sd.conv_res']))
        buf('sd.conv1_plus_res line buffer', '3x3 line buffer (3 rows)', 3 * w * c)
        buf('sd.conv1.shuffle row (main)', 'pixel-shuffle row buffer (1 row)', 2 * w * n)
        # one output row behind the main shuffle: the IGDN takes ~13 cycles per element, so reading the shuffle row
        # buffer out directly into it would stall the merged engine for that time on every input row
        buf('sd.conv1.shuffle drain (main)', 'pixel-shuffle drain FIFO (1 row)', 2 * w * n)
        buf('sd.conv_res.shuffle row (main)', 'pixel-shuffle row buffer (1 row)', 2 * w * n)
        E.append(self.gdn('sd.igdn', n, 2 * h, 2 * w))
        buf('sd.sum skip delay', 'skip delay FIFO (1 row)', 2 * w * n, 'sd.sum.b')
        co = self.dec.conv2.conv.out_channels
        E.append(self.engine('sd.conv2', n, co, 3, 2 * h, 2 * w))
        buf('sd.conv2 line buffer', '3x3 line buffer (3 rows)', 3 * 2 * w * n)
        buf('sd.conv2.shuffle row (main)', 'pixel-shuffle row buffer (1 row)', 4 * w * co // 4)
        return E, B


def memory_plan_section(pl, wbits, abits):
    out = {'source': 'export_shallow.py (memory_plan.Planner rules)', 'fclk_hz': 250e6, 'fps': 30, 'bram36_target': 100}
    for part in ('encoder', 'decoder'):
        engines, buffers = pl.place(part, 100)
        out[part] = {'engines': engines, 'buffers': buffers}
    return out


# ---------------------------------------------------------------------- golden vectors
def golden_images(cache, kodak, n_val=2):
    val = np.load(Path(cache) / 'val_center256.npy')[:n_val]
    imgs = [(f'div2k_val_{i:02d}', val[i]) for i in range(n_val)]
    k = np.asarray(Image.open(Path(kodak) / 'kodim23.png').convert('RGB'))
    h, w = k.shape[:2]
    imgs.append(('kodim23_center256', k[(h - 256) // 2:(h + 256) // 2, (w - 256) // 2:(w + 256) // 2]))
    return imgs


@torch.inference_mode()
def run_golden(ib, enc, dec, img_hwc, dev, snr):
    ib.capture = {}
    x = torch.from_numpy(np.ascontiguousarray(img_hwc)).permute(2, 0, 1)[None].to(dev)
    idx = IR.encode(ib, enc, x).long()
    gen = torch.Generator(device=dev).manual_seed(NOISE_SEED)
    noise = torch.randn(idx.shape, generator=gen, device=dev) * math.sqrt(10 ** (-snr / 10) / 2)
    q10 = to_q10(symbols(idx) + noise)
    ib.capture['rx_symbols'] = q10.detach().cpu()
    ib.capture['tx_symbols_q10'] = to_q10(symbols(idx)).detach().cpu()
    Q.shallow_decode(ib, dec, iq_to_latent(q10, 16, x.shape[2] // 4, x.shape[3] // 4))
    cap, ib.capture = ib.capture, None
    return cap


def export_pwl(ib, root):
    """parameter files of the PWL ops (added to their manifest entries)"""
    pdir = root / 'params'
    out = {}
    for op in ib.graph:
        if op['op'] != 'pwl':
            continue
        name = op['name']
        s = op['slope'].cpu().numpy().astype(np.int64)
        y = op['yk'].cpu().numpy().astype(np.int64)
        yb = bits_needed(y)
        assert np.abs(s).max() <= 127 and yb <= 32, (name, yb)
        assert len(set(np.asarray(op['rq_pre']).tolist())) == 1, f'{name}: pre differs per channel'
        assert np.asarray(op['rq_sh']).min() >= 1, name
        out[name] = {
            'slope': write_mem(pdir / f'{name}.slope.mem', s, 8) | {'layout': '[C][NK+1]', 'signed': True},
            'yk': write_mem(pdir / f'{name}.yk.mem', y, yb) | {'layout': '[C][NK]', 'signed': True},
            'rq_pre': write_mem(pdir / f'{name}.rq_pre.mem', op['rq_pre'], 8) | {'signed': False},
            'rq_M': write_mem(pdir / f'{name}.rq_m.mem', op['rq_M'], 17) | {'signed': False},
            'rq_sh': write_mem(pdir / f'{name}.rq_sh.mem', op['rq_sh'], 8) | {'signed': False},
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ckpt', required=True)
    ap.add_argument('--cache', required=True)
    ap.add_argument('--kodak', required=True)
    ap.add_argument('--out-dir', default=str(REPO / 'data' / 'model_shallow'))
    ap.add_argument('--wbits', type=int, default=8)
    ap.add_argument('--abits', type=int, default=12)
    ap.add_argument('--headroom', type=float, default=1.5)
    ap.add_argument('--snr-db', type=float, default=10.0)
    a = ap.parse_args()
    torch.backends.cudnn.allow_tf32 = False; torch.backends.cuda.matmul.allow_tf32 = False
    dev = torch.device('cuda')
    root = Path(a.out_dir); root.mkdir(parents=True, exist_ok=True)
    dep, enc, dec, ck = Q.load_models(a.ckpt, dev)
    Q.IntB.PWL_FORM = 'knot'
    st = Q.calibrate(enc, dec, a.cache, dev)
    sc = Q.pwl_input_scales(IR.scales_from_stats(st, a.headroom, a.abits, {'sd.igdn': 2.0}), st, a.abits)

    # accuracy of exactly this configuration (same as quant_shallow.py)
    st_dep = Q.calibrate_deployed(dep, a.cache, dev)
    sc_dep = IR.scales_from_stats(st_dep, a.headroom, a.abits, {'dec.9.igdn': 2.0})
    val, kod = Q.load_images(a.cache, a.kodak)
    acc = {}
    for nm, imgs in (('val', val), ('kodak', kod)):
        r, ib_k = Q.evaluate(imgs, a.snr_db, dev, enc, dec, sc, st, dep, sc_dep, st_dep, a.wbits, a.abits)
        acc[nm] = r
        print(nm, {k: round(v, 3) for k, v in r.items()}, flush=True)
    print('saturation (Kodak):', {k: v for k, v in ib_k.saturation.items() if v} or 'none', flush=True)

    ib = Q.IntB(sc, st, 32, 32, a.wbits, a.abits)
    golden = {}
    for img_name, img in golden_images(a.cache, a.kodak):
        cap = run_golden(ib, enc, dec, img, dev, a.snr_db)
        files = {}
        fmt = {op['out']: tensor_format(op, ib) for op in ib.graph}
        for tname, t in cap.items():
            if tname in ('rx_symbols', 'tx_symbols_q10'):
                iq = t.numpy().astype(np.int64)[0]
                word = ((iq[:, 1] & 0xFFF) << 12) | (iq[:, 0] & 0xFFF)
                name = {'rx_symbols': 'rx_iq24', 'tx_symbols_q10': 'tx_iq24'}[tname]
                files[name] = write_mem(root / 'golden' / img_name / f'{name}.mem', word, 24) | {
                    'shape': [int(iq.shape[0])], 'layout': '[symbol] {Q[23:12], I[11:0]} Q10', 'signed': False}
                if tname == 'tx_symbols_q10':
                    continue
                bits, signed = 12, True
            else:
                bits, signed = fmt[tname]
            arr = nhwc(t)
            files[tname] = write_mem(root / 'golden' / img_name / f'{tname}.mem', arr, bits) | {
                'shape': list(arr.shape[1:]), 'layout': 'HWC' if arr.ndim == 4 else '[symbol][I,Q]', 'signed': signed}
        rel_files(files, root)
        golden[img_name] = files
        print('golden', img_name, len(files), 'tensors', flush=True)

    pwl_files = export_pwl(ib, root)
    ops, tables = export_params(ib, root, root)
    for o in ops:
        if o['op'] == 'pwl':
            for k in ('slope', 'yk'):
                o.pop(k, None)
            o['files'] = pwl_files[o['name']]
            rel_files(o['files'], root)
    tensors = {}
    for op in ib.graph:
        bits, signed = tensor_format(op, ib)
        t = golden[next(iter(golden))].get(op['out'])
        tensors[op['out']] = {'bits': bits, 'signed': signed, 'shape_hwc': t['shape'] if t else None,
                              'scale': op.get('out_scale', op.get('scale'))}
    pl = ShallowPlanner(dec, a.wbits, a.abits, 30, 250e6, 0.8)
    manifest = {
        'model': 'DeepJSCC-Q encoder (JSAIT 2022 architecture, jointly fine-tuned) + shallow decoder '
                 '(linear 3x3, 4 PWL residual blocks, 2 sub-pixel upsamplings), C=32, Cout=16, 64-QAM',
        'memory_plan': memory_plan_section(pl, a.wbits, a.abits),
        'checkpoint': str(a.ckpt), 'epoch': ck.get('epoch'), 'train_args': {k: v for k, v in ck['args'].items()
                                                                         if isinstance(v, (int, float, str, bool))},
        'format': {'weights': f'int{a.wbits} signed, per-output-channel scale, OHWI',
                   'activations': f'int{a.abits} signed, per-tensor scale, NHWC',
                   'mem': 'one value per line, two\'s complement hex, digits = ceil(bits/4)',
                   'golden_input': 'uint8 RGB, 256x256',
                   'channel': f'AWGN {a.snr_db} dB on the unit-power 64-QAM symbols, seed {NOISE_SEED}; '
                              'rx_symbols = receiver output Q10 (12 bit) per symbol [I,Q]',
                   'latent_pairing': 'as data/model (NHWC stream order, channels (2j, 2j+1) = (I, Q))'},
        'arithmetic': {
            'pwl': 's = clamp(((x - T0) >>> STEP_LOG2) + 1, 0, NK); k = max(s - 1, 0); '
                   'acc = yk[c][k] + slope[c][s] * (x - (T0 + (k << STEP_LOG2))); '
                   'y = satA(rsr(sat27(rsr(acc, pre[c])) * M[c], sh[c]))',
            'other ops': 'as data/model/manifest.json'},
        'tensors': tensors, 'ops': ops, 'sigmoid_tables': tables, 'golden': golden,
        'accuracy_db': acc,
        'max_abs_internal': {k: v for k, v in sorted(ib.width.items())},
    }
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=1, default=lambda o: o.tolist()
                                                   if hasattr(o, 'tolist') else float(o)), encoding='utf-8')
    print('exported', len(ops), 'ops to', root, flush=True)


if __name__ == '__main__':
    main()
