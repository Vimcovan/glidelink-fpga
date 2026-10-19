"""Golden vectors for the RTL simulations, computed with the NumPy integer model (no PyTorch, no GPU).

    python sim/network/make_golden.py [--input image] [--name div2k_val_00] [--snr 10]

-> <export>/golden/<name>/<tensor>.mem (default export data/model_shallow) for every tensor of manifest.json, plus rx_iq24.mem / tx_iq24.mem
(24-bit {Q, I} Q10 words of the decoder input / encoder output), in the format of export_fpga.py. The default
name is the one the simulation scripts use (-Image div2k_val_00).

The vectors of the original export (DIV2K validation crops, AWGN drawn with PyTorch's generator, 362 MB) are not
in the repository. These use any image, centre-cropped and resized to 256x256 (default: the hardware photo of the
RX GUI), and NumPy noise, so the values differ from the original ones, but the RTL must still match them bit for
bit and the cycle counts are the same (the schedule does not depend on the data).
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
from PIL import Image

from golden_np import Golden, rsr, sat

REPO = Path(__file__).resolve().parents[2]
LEVELS = np.array([-7., -5., -3., -1., 1., 3., 5., 7.]) / math.sqrt(42.0)     # 64-QAM per axis, unit power


def write_mem(path: Path, vals, bits: int):
    v = np.asarray(vals, dtype=np.int64).ravel()
    assert -(1 << (bits - 1)) <= v.min(initial=0) and v.max(initial=0) < (1 << bits), (path.name, bits)
    digits = (bits + 3) // 4
    path.write_text(''.join(f'{x:0{digits}X}\n' for x in (v & ((1 << bits) - 1)).tolist()), encoding='ascii',
                    newline='\n')


def load_image(path: Path) -> np.ndarray:
    im = Image.open(path).convert('RGB')
    s = min(im.size)
    im = im.crop(((im.width - s) // 2, (im.height - s) // 2, (im.width + s) // 2, (im.height + s) // 2))
    return np.asarray(im.resize((256, 256), Image.LANCZOS), dtype=np.int64)


def forward(g: Golden, img: np.ndarray, snr: float | None, seed: int):
    """Run the recorded ops in order; returns {tensor: int64 array} and the tensor formats {name: (bits, signed)}."""
    T, fmt = {'input': img}, {'input': (8, False)}
    for op in g.m['ops']:
        kind, out = op['op'], op['out']
        if kind == 'input':
            continue
        if kind == 'rx_input':
            idx = T['latent_idx']                                            # (N, 2) levels 0..7, [I, Q]
            y = LEVELS[idx]
            T['tx_symbols_q10'] = np.clip(np.round(y * 1024), -2048, 2047).astype(np.int64)
            if snr is not None:
                y = y + np.random.default_rng(seed).standard_normal(y.shape) * math.sqrt(10 ** (-snr / 10) / 2)
            q10 = np.clip(np.round(y * 1024), -2048, 2047).astype(np.int64)
            T['rx_symbols'] = q10
            T[out] = q10.reshape(g.m['tensors'][out]['shape_hwc'])          # NHWC element sequence
            fmt[out] = (12, True)
            continue
        if kind == 'conv':
            y = g.conv(op, T[op['in']])
        elif kind == 'add':
            y = g.add_core(op, T[op['in'][0]], T[op['in'][1]])
            y = sat(np.maximum(y, 0) if op['relu'] else y, g.abits)
        elif kind == 'gate':
            x, a, s = (T[n] for n in op['in'])
            y = sat(g.add_core(op, x, rsr(a * s, 16)), g.abits)
        elif kind in ('gdn', 'igdn'):
            y = g.gdn(op, T[op['in']])
        elif kind == 'pwl':
            y = g.pwl(op, T[op['in']])
        elif kind == 'sigmoid':
            y = g.sigmoid_q16(T[op['in']], op, g.tabs[op['table']])
        elif kind == 'pixel_shuffle':
            y = g.pixel_shuffle(T[op['in']], op['factor'])
        elif kind == 'qam':
            thr = np.array(op['thresholds'], dtype=np.int64)
            y = (T[op['in']].reshape(-1)[:, None] > thr).sum(1).reshape(-1, 2)
        elif kind == 'sigmoid_out':
            s = g.sigmoid_q16(T[op['in']], op, g.tabs[op['table']])
            y = np.clip(rsr(s * 255, 16), 0, 255)
        else:
            raise ValueError(kind)
        T[out] = y
        fmt[out] = {'sigmoid': (17, False), 'sigmoid_out': (8, False), 'qam': (3, False)}.get(kind, (g.abits, True))
    return T, fmt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', type=Path, default=REPO / 'data' / 'model_shallow')
    ap.add_argument('--input', type=Path, default=REPO / 'src' / 'sw' / 'rx' / 'assets' / 'about' / 'hardware.jpg')
    ap.add_argument('--name', default='div2k_val_00')
    ap.add_argument('--snr', type=float, default=10.0, help='AWGN on the unit-power symbols (dB); < 0: no noise')
    ap.add_argument('--seed', type=int, default=20260928)
    a = ap.parse_args()
    g = Golden(a.export)
    T, fmt = forward(g, load_image(a.input), a.snr if a.snr >= 0 else None, a.seed)
    d = a.export / 'golden' / a.name
    d.mkdir(parents=True, exist_ok=True)
    for name in ('rx_symbols', 'tx_symbols_q10'):
        iq = T[name]
        write_mem(d / {'rx_symbols': 'rx_iq24.mem', 'tx_symbols_q10': 'tx_iq24.mem'}[name],
                  ((iq[:, 1] & 0xFFF) << 12) | (iq[:, 0] & 0xFFF), 24)
    write_mem(d / 'rx_symbols.mem', T['rx_symbols'], 12)
    for name, (bits, _) in fmt.items():
        write_mem(d / f'{name}.mem', T[name], bits)
    print(f'{d}: {len(fmt) + 3} files from {a.input.name}, SNR {a.snr} dB')


if __name__ == '__main__':
    main()
