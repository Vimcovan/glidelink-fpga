"""Stand-alone NumPy int64 golden model of the exported integer DeepJSCC-Q (W8A12).

Reads only manifest.json and the .mem files written by export_fpga.py (no PyTorch
model), executes the recorded ops in order and compares every tensor with the
exported golden vectors.  Bit-exact agreement proves that the export is complete and
that the integer semantics in the manifest are sufficient to rebuild the network.

  python golden_np.py --export runs/fpga_export_w8a12 [--image kodim23_center256]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def read_mem(path: Path, bits: int, signed: bool) -> np.ndarray:
    v = np.array([int(x, 16) for x in path.read_text(encoding='ascii').split()], dtype=np.int64)
    if signed:
        v = np.where(v >= (1 << (bits - 1)), v - (1 << bits), v)
    return v


def rsr(v: np.ndarray, k) -> np.ndarray:
    """Round-half-up arithmetic right shift; k may be an array; k < 0 = left shift."""
    k = np.asarray(k, dtype=np.int64)
    kp = np.maximum(k, 0)
    rnd = np.where(kp > 0, np.left_shift(np.int64(1), np.maximum(kp - 1, 0)), 0)
    return np.left_shift((v + rnd) >> kp, np.maximum(-k, 0))


def sat(v, bits):
    return np.clip(v, -(1 << (bits - 1)), (1 << (bits - 1)) - 1)


def isqrt(d: np.ndarray) -> np.ndarray:
    r = np.floor(np.sqrt(d.astype(np.float64))).astype(np.int64)
    r -= (r * r > d)
    r += ((r + 1) * (r + 1) <= d)
    return r


class Golden:
    def __init__(self, root: Path):
        self.root = root
        self.m = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
        self.abits = int(self.m['format']['activations'].split('int')[1].split()[0])
        self.tabs = {k: {**t, 'c0': self.load(t['c0']), 'c1': self.load(t['c1'])}
                     for k, t in self.m['sigmoid_tables'].items()}

    def load(self, f):
        return read_mem(self.root / f['file'], f['bits'], f.get('signed', True))

    # ------------------------------------------------------------------ ops
    def conv(self, op, x):
        f = op['files']
        kh, kw = op['k']; sh_, sw_ = op['stride']; ph, pw = op['pad']
        w = self.load(f['weight']).reshape(op['cout'], kh, kw, op['cin'])
        b = self.load(f['bias'])
        xp = np.pad(x, ((ph, ph), (pw, pw), (0, 0)))
        win = sliding_window_view(xp, (kh, kw), axis=(0, 1))[::sh_, ::sw_]      # (Ho, Wo, C, kh, kw)
        ho, wo = win.shape[:2]
        cols = win.transpose(0, 1, 3, 4, 2).reshape(ho * wo, kh * kw * op['cin'])
        acc = cols @ w.reshape(op['cout'], -1).T + b                            # int64 matmul
        if op['act'] == 'relu':
            acc = np.maximum(acc, 0)
        elif op['act'] == 'leaky':
            acc = np.where(acc < 0, rsr(acc, 7), acc)
        y = rsr(sat(rsr(acc, self.load(f['rq_pre'])), 27) * self.load(f['rq_M']), self.load(f['rq_sh']))
        return sat(y, self.abits).reshape(ho, wo, op['cout'])

    def add_core(self, op, a, b):
        return rsr(a * op['Ma'] + b * op['Mb'], op['sh'])

    def gdn(self, op, x):
        f = op['files']; c = op['channels']
        g = self.load(f['gamma']).reshape(c, c)
        beta = self.load(f['beta'])
        x2 = rsr(x * x, op['x2_shift'])
        d = (x2 @ g.T + beta) << op['L']
        r = isqrt(d)
        if op['op'] == 'igdn':
            t = x * r
        else:
            t = np.sign(x) * ((np.abs(x) << op['F']) // r)
        rq = op['rq']
        return sat(rsr(sat(rsr(t, rq['pre']), 27) * rq['M'], rq['sh']), self.abits)

    def pwl(self, op, x):
        """per-channel piecewise-linear activation (axis_pwl): segment s = clamp(((x - T0) >> L) + 1, 0, NK), left knot
        k = max(s - 1, 0), acc = Y[c][k] + S[c][s] * (x - T0 - k 2^L), then the per-channel rescale of a convolution"""
        f = op['files']; c, nk, t0, l = op['channels'], op['NK'], op['T0'], op['STEP_LOG2']
        slope = self.load(f['slope']).reshape(c, nk + 1)
        yk = self.load(f['yk']).reshape(c, nk)
        seg = np.clip(((x - t0) >> l) + 1, 0, nk)
        k = np.maximum(seg - 1, 0)
        ch = np.arange(c)
        acc = yk[ch, k] + slope[ch, seg] * (x - t0 - (k << l))
        y = rsr(sat(rsr(acc, self.load(f['rq_pre'])), 27) * self.load(f['rq_M']), self.load(f['rq_sh']))
        return sat(y, self.abits)

    def sigmoid_q16(self, z, op, tab):
        u = np.abs(z)
        v = rsr(u * op['to_q312_M'], op['to_q312_sh'])
        big = v >= (1 << 15)
        v = np.minimum(v, (1 << 15) - 1)
        ob = tab['offset_bits']
        seg = v >> ob
        off = v & ((1 << ob) - 1)
        g = np.clip(tab['c0'][seg] + rsr(tab['c1'][seg] * off, tab['shift']), 0, 65536)
        g = np.where(big, 65536, g)
        return np.where(z < 0, 65536 - g, g)

    @staticmethod
    def pixel_shuffle(x, r):
        h, w, c = x.shape
        co = c // (r * r)
        return x.reshape(h, w, co, r, r).transpose(0, 3, 1, 4, 2).reshape(h * r, w * r, co)

    # ------------------------------------------------------------------ run
    def run(self, image: str, verbose=False):
        gold = self.m['golden'][image]
        load_t = lambda name: self.load(gold[name]).reshape(gold[name]['shape'])
        T = {'input': load_t('input')}
        mism = {}

        def check(name, val):
            ref = load_t(name)
            bad = int((val.reshape(ref.shape) != ref).sum())
            if bad:
                mism[name] = bad
            T[name] = ref            # continue from the golden value so one error does not cascade

        for op in self.m['ops']:
            kind, out = op['op'], op['out']
            if kind == 'input':
                continue
            if kind == 'rx_input':
                # the receiver symbols are the NHWC element sequence of the decoder input
                sym = load_t('rx_symbols')                                   # (N, 2)
                check(out, sym.reshape(gold['rx_in']['shape']))
                continue
            if kind == 'conv':
                y = self.conv(op, T[op['in']])
            elif kind == 'add':
                a, b = T[op['in'][0]], T[op['in'][1]]
                y = self.add_core(op, a, b)
                y = sat(np.maximum(y, 0) if op['relu'] else y, self.abits)
            elif kind == 'gate':
                x, a, g = (T[n] for n in op['in'])
                y = sat(self.add_core(op, x, rsr(a * g, 16)), self.abits)
            elif kind in ('gdn', 'igdn'):
                y = self.gdn(op, T[op['in']])
            elif kind == 'pwl':
                y = self.pwl(op, T[op['in']])
            elif kind == 'sigmoid':
                y = self.sigmoid_q16(T[op['in']], op, self.tabs[op['table']])
            elif kind == 'pixel_shuffle':
                y = self.pixel_shuffle(T[op['in']], op['factor'])
            elif kind == 'qam':
                z = T[op['in']]                                              # HWC
                flat = z.reshape(-1)                                         # NHWC stream order
                thr = np.array(op['thresholds'], dtype=np.int64)
                y = (flat[:, None] > thr).sum(1).reshape(-1, 2)
            elif kind == 'sigmoid_out':
                g = self.sigmoid_q16(T[op['in']], op, self.tabs[op['table']])
                y = np.clip(rsr(g * 255, 16), 0, 255)
            else:
                raise ValueError(kind)
            check(out, y)
            if verbose:
                print(f'  {kind:13s} {out:28s} {"OK" if out not in mism else f"{mism[out]} mismatches"}')
        return mism


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default=str(Path(__file__).resolve().parents[2] / 'data' / 'model_shallow'))
    ap.add_argument('--image', default=None)
    ap.add_argument('--verbose', action='store_true')
    a = ap.parse_args()
    g = Golden(Path(a.export))
    images = [a.image] if a.image else list(g.m['golden'])
    ok = True
    for img in images:
        t = time.time()
        mism = g.run(img, a.verbose)
        n_ops = len(g.m['ops'])
        print(f'{img}: {n_ops} ops, {"ALL BIT-EXACT" if not mism else f"MISMATCH in {len(mism)} tensors: {mism}"} '
              f'({time.time() - t:.1f} s)')
        ok &= not mism
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
