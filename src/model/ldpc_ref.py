"""Bit-exact reference of the SSCC LDPC chain in the PL (src/hw/{tx,rx}/rtl/ps/ldpc_*.sv) and generator of its code
table package.

Code: IEEE 802.11n LDPC, n = 1944, rate 2/3 (k = 1296), lifting Z = 81, base matrix 8 x 24 (shifts from the Vivado
LDPC IP table ldpc_v2_0/common_tcl/wifi_decode.yml). Block (r, c) with shift s connects check r*Z + i to variable
c*Z + (i + s) mod Z.

Encoder (ldpc_enc): lambda_r = sum_c<16 P^s(r,c) u_c; the three blocks of column 16 have shifts 1, 0, 1, so their sum
is the identity and p_0 = sum_r lambda_r; p_1 = lambda_0 + P^s(0,16) p_0, p_(r+1) = lambda_r + P^s(r,16) p_0 + p_r.

Decoder (ldpc_dec): layered normalized min-sum, one layer = the Z checks of a base row, integer arithmetic:
  channel LLR L (LW = 7 bits, |L| <= 63, > 0: bit 0), APP initialised to L (AW = 11 bits)
  v2c = sat_MW(APP - c2v_old)          (MW = 9 bits, |v2c| <= 255; c2v_old = 0 in the first iteration)
  per check: min1, min2 of |v2c|, position of min1, sign product
  c2v = sign * (m - (m >> 2)) with m = min2 at the min1 position, else min1
  APP = sat_AW(v2c + c2v)
Stop after the first iteration in which every check of every layer was satisfied by the signs of the APP values it
read and no APP sign changed; at most MAX_ITER iterations. Output: signs of the 1296 information bits.
Demapper (qam64_softdemap): max-log per axis on the 12-bit symbol (levels 158 * {+-1..7}), |L| = min(|x| >> 3, 63).

    python ldpc_ref.py --gen-pkg              writes the SystemVerilog code table package (TX and RX copies)
    python ldpc_ref.py --selftest
"""
import argparse
import re
from pathlib import Path

import numpy as np

Z, NB, NL, KB = 81, 24, 8, 16
N, K = Z * NB, Z * KB
LW, AW, MW = 7, 11, 9
MAX_ITER = 16
YML = 'D:/AMDDesignTools/2025.2/Vivado/data/ip/xilinx/ldpc_v2_0/common_tcl/wifi_decode.yml'
ROOT = Path(__file__).resolve().parents[2]


def base_matrix(yml=YML):
    txt = Path(yml).read_text()
    blk = txt.split('wifi802_11_cr2_3_1944_decode:')[1].split('wifi802_11_')[0]
    S = -np.ones((NL, NB), int)
    for r, c, s in re.findall(r'row: (\d+), col: (\d+), shift: (\d+)', blk):
        S[int(r), int(c)] = int(s)
    return S


def layer_orders(S):
    """column order inside each layer (read order = write order); columns shared with the previous layer last, shared
    with the next layer first -> fewer stalls of the overlapped layer pipeline (scoreboard)"""
    L = [[c for c in range(NB) if S[l, c] >= 0] for l in range(NL)]
    out = []
    for l in range(NL):
        prev, nxt = set(L[(l - 1) % NL]), set(L[(l + 1) % NL])
        out.append(sorted(L[l], key=lambda c: ((c in prev), -(c in nxt), c)))
    return out


def rot(v, s):
    """lane i <- v[(i + s) mod Z] (v: (..., Z))"""
    return np.roll(v, -s, axis=-1)


def encode(S, u):
    """u: (K,) bits -> (N,) codeword, systematic"""
    uc = u.reshape(KB, Z)
    lam = np.zeros((NL, Z), np.uint8)
    for r in range(NL):
        for c in range(KB):
            if S[r, c] >= 0:
                lam[r] ^= rot(uc[c], S[r, c])
    p = np.zeros((NL, Z), np.uint8)
    p[0] = np.bitwise_xor.reduce(lam, axis=0)
    h = lambda r: rot(p[0], S[r, KB]) if S[r, KB] >= 0 else 0
    p[1] = lam[0] ^ h(0)
    for r in range(1, NL - 1):
        p[r + 1] = lam[r] ^ h(r) ^ p[r]
    return np.concatenate([u, p.reshape(-1)])


def check(S, x):
    xc = x.reshape(NB, Z)
    for r in range(NL):
        acc = np.zeros(Z, np.uint8)
        for c in range(NB):
            if S[r, c] >= 0:
                acc ^= rot(xc[c], S[r, c])
        if acc.any():
            return False
    return True


def sat(x, w):
    m = 2 ** (w - 1) - 1
    return np.clip(x, -m, m)


def decode(S, L, max_iter=MAX_ITER, orders=None):
    """L: (N,) int channel LLRs -> (hard bits (N,), ok, iterations).  Bit-exact model of ldpc_dec."""
    orders = orders or layer_orders(S)
    app = np.array(L, np.int64).reshape(NB, Z)
    m1s = np.zeros((NL, Z), np.int64); m2s = np.zeros((NL, Z), np.int64)
    idx = np.zeros((NL, Z), np.int64); csg = np.zeros((NL, 11, Z), bool)     # stored c2v: scaled mins, signs
    for it in range(max_iter):
        all_ok, flip = True, False
        for l in range(NL):
            cols = orders[l]
            a = np.stack([rot(app[c], S[l, c]) for c in cols])                 # (11, Z)
            if it:
                mag = np.where(np.arange(11)[:, None] == idx[l], m2s[l], m1s[l])
                old = np.where(csg[l], -mag, mag)
            else:
                old = np.zeros_like(a)
            all_ok &= not np.bitwise_xor.reduce(a < 0, axis=0).any()
            v = sat(a - old, MW)
            mg = np.abs(v)
            i1 = np.argmin(mg, axis=0)                                         # first minimum (lowest k)
            m1 = mg[i1, np.arange(Z)]
            mg2 = mg.copy(); mg2[i1, np.arange(Z)] = 1 << 20
            m2 = mg2.min(axis=0)
            sg = v < 0
            prod = np.bitwise_xor.reduce(sg, axis=0)
            m1s[l], m2s[l], idx[l] = m1 - (m1 >> 2), m2 - (m2 >> 2), i1
            csg[l] = sg ^ prod
            mag = np.where(np.arange(11)[:, None] == idx[l], m2s[l], m1s[l])
            new = sat(v + np.where(csg[l], -mag, mag), AW)
            flip |= ((new < 0) != (a < 0)).any()
            for k, c in enumerate(cols):
                app[c] = rot(new[k], -S[l, c])
        if all_ok and not flip:
            return (app.reshape(-1) < 0).astype(np.uint8), True, it + 1
    return (app.reshape(-1) < 0).astype(np.uint8), False, max_iter


A = 158


def qam_map(bits):
    """(6m,) bits -> (m, 2) int levels [I, Q] as qam64_map"""
    b = np.asarray(bits, np.int64).reshape(-1, 2, 3)
    g = b[..., 0] * 4 + b[..., 1] * 2 + b[..., 2]
    k = np.zeros_like(g)
    k |= (g >> 2) << 2
    k |= (((g >> 2) ^ (g >> 1)) & 1) << 1
    k |= ((g >> 2) ^ (g >> 1) ^ g) & 1
    return (2 * k - 7) * A


def demap(y):
    """(m, 2) int received [I, Q] (12 bit) -> (6m,) int LLRs (> 0: bit 0) as qam64_softdemap (SHIFT 3, MAG 63)"""
    out = []
    for ax in (0, 1):
        x = y[:, ax].astype(np.int64)
        ax_ = np.abs(x)
        ls = np.stack([x, 4 * A - ax_, 2 * A - np.abs(ax_ - 4 * A)], -1)
        mag = np.minimum(np.abs(ls) >> 3, 63)
        out.append(np.where(ls > 0, -mag, mag))
    return np.stack(out, 1).reshape(-1)


def gen_pkg():
    S = base_matrix()
    orders = layer_orders(S)
    lines = ['// GENERATED by src/model/ldpc_ref.py --gen-pkg: IEEE 802.11n LDPC n = 1944, rate 2/3 (Z = 81), from the',
             '// Vivado LDPC IP code table. Block (r, c) with shift s connects check r*Z+i to variable c*Z+(i+s) mod Z.',
             'package ldpc_r23_pkg;',
             f'    localparam int Z = {Z}, NB = {NB}, NL = {NL}, KB = {KB}, DC = 11;',
             '    // base matrix shift, -1 = zero block',
             '    localparam int SHIFT [NL][NB] = \'{']
    lines += ['        \'{' + ', '.join(f'{v:2d}' for v in S[r]) + '}' + (',' if r < NL - 1 else '') for r in range(NL)]
    lines += ['    };',
              '    // decoder: column order inside each layer (read = write order)',
              '    localparam int ORDER [NL][DC] = \'{']
    lines += ['        \'{' + ', '.join(f'{v:2d}' for v in orders[r]) + '}' + (',' if r < NL - 1 else '') for r in range(NL)]
    lines += ['    };',
              '    // the same tables as functions (case): a variable index into the arrays above is not reliable in xsim',
              '    function automatic logic signed [7:0] shift_of(input logic [2:0] r, input logic [4:0] c);',
              '        case ({r, c})']
    lines += [f"            {{3'd{r}, 5'd{c:2d}}}: return 8'sd{S[r, c]};" for r in range(NL) for c in range(NB) if S[r, c] >= 0]
    lines += ["            default: return -8'sd1;", '        endcase', '    endfunction',
              '    function automatic logic [4:0] order_of(input logic [2:0] l, input logic [3:0] k);',
              '        case ({l, k})']
    lines += [f"            {{3'd{l}, 4'd{k:2d}}}: return 5'd{orders[l][k]};" for l in range(NL) for k in range(11)]
    lines += ["            default: return 5'd0;", '        endcase', '    endfunction', 'endpackage', '']
    for side in ('tx', 'rx'):
        p = ROOT / f'src/hw/{side}/rtl/ps/ldpc_r23_pkg.sv'
        p.write_text('\n'.join(lines), encoding='utf-8', newline='\n')
        print('wrote', p)


def selftest():
    S = base_matrix()
    rng = np.random.default_rng(0)
    for _ in range(20):
        u = rng.integers(0, 2, K).astype(np.uint8)
        x = encode(S, u)
        assert check(S, x)
        L = np.where(x == 0, 40, -40)
        h, ok, it = decode(S, L)
        assert ok and (h == x).all() and it == 1, it
    errs = 0
    for _ in range(50):
        u = rng.integers(0, 2, K).astype(np.uint8)
        x = encode(S, u)
        sym = qam_map(np.r_[x])
        y = np.clip(np.round(sym + rng.normal(0, A * np.sqrt(42) / np.sqrt(2) * 10 ** (-15.5 / 20), sym.shape)), -2048, 2047)
        h, ok, it = decode(S, demap(y.astype(int)))
        errs += (h[:K] != u).any()
    print('selftest ok; 15.5 dB codeword errors', errs, '/ 50')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--gen-pkg', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.gen_pkg:
        gen_pkg()
    if a.selftest:
        selftest()
