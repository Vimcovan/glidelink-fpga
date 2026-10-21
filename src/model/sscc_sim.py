"""SSCC baselines in simulation: JPEG + channel code + 64-QAM over AWGN, with the frame of the system (32,768 64-QAM
symbols = 196,608 coded bits per 256x256 image), against DeepJSCC-Q.

Channel codes
  cc       K=7 (171, 133) rate-1/2 convolutional code, 128 zero tail bits, soft Viterbi (the deployed baseline)
  ldpcR    IEEE 802.11n LDPC, n = 1944, rate R in 1/2, 2/3, 3/4, 5/6; parity-check matrices from the code tables of
           the Vivado LDPC IP (ldpc_v2_0/common_tcl/wifi_decode.yml); 101 codewords per frame (+264 pad bits);
           normalized min-sum (scale 0.75), flooding, ITER iterations
Source: JPEG (IJG libjpeg through PIL, 4:2:0, the largest quality whose file fits the payload; packet = 12-byte header
+ JPEG, as src/sw/common/sscc_pkt.py), payload = information bits of the frame. Bits scrambled (PRBS) before coding.
Receiver: exact 64-QAM LLRs (Gray per axis, unit average power); a packet whose JPEG does not decode counts as the
mid-gray image (as the receiver GUI).  SNR = Es/N0 of the complex symbols (same definition as the DeepJSCC-Q sweeps).
PSNR: MSE pooled over the images (as int_sweep.py).

    python sscc_sim.py --cache <data/cache> --vivado <Vivado install> --out <json> [--n 100] [--codes cc,ldpc1/2,...]
"""
import argparse
import io
import json
import math
import re
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

FRAME_BITS = 32768 * 6
HDR = 12
LEVELS = np.array([-7, -5, -3, -1, 1, 3, 5, 7]) / math.sqrt(42.0)
GRAY = {0b000: -7, 0b001: -5, 0b011: -3, 0b010: -1, 0b110: 1, 0b111: 3, 0b101: 5, 0b100: 7}   # 802.11a per axis
ITER = 40
dev = torch.device('cuda')


# ---------------------------------------------------------------- 64-QAM
AX_BITS = np.zeros((8, 3), np.float32)                         # level index -> 3 bits (Gray)
for b, lv in GRAY.items():
    AX_BITS[(lv + 7) // 2] = [(b >> 2) & 1, (b >> 1) & 1, b & 1]
BITS_TO_LV = {tuple(int(x) for x in AX_BITS[i]): i for i in range(8)}


def modulate(bits):                                            # (N, 196608) {0,1} -> (N, 32768) complex
    b = bits.view(bits.shape[0], -1, 2, 3).long()
    idx = b[..., 0] * 4 + b[..., 1] * 2 + b[..., 2]           # 3-bit code -> level index via table
    tab = torch.zeros(8, dtype=torch.long, device=bits.device)
    for code, li in BITS_TO_LV.items():
        tab[code[0] * 4 + code[1] * 2 + code[2]] = li
    lv = torch.tensor(LEVELS, dtype=torch.float32, device=bits.device)[tab[idx]]   # (N, S, 2) [I, Q]
    return torch.complex(lv[..., 0], lv[..., 1])


def llr(y, snr_db):                                            # exact LLR log P(b=0)/P(b=1), (N, 196608)
    n0 = 10 ** (-snr_db / 10)                                  # complex noise power; per axis n0/2
    lv = torch.tensor(LEVELS, dtype=torch.float32, device=y.device)
    ab = torch.tensor(AX_BITS, device=y.device)                # (8, 3)
    out = []
    for comp in (y.real, y.imag):
        d = -(comp[..., None] - lv) ** 2 / n0                  # (N, S, 8) log-likelihood per level
        l = []
        for k in range(3):
            l0 = torch.logsumexp(d.masked_fill(ab[:, k] == 1, -1e30), -1)
            l1 = torch.logsumexp(d.masked_fill(ab[:, k] == 0, -1e30), -1)
            l.append(l0 - l1)
        out.append(torch.stack(l, -1))                         # (N, S, 3)
    return torch.stack(out, -2).reshape(y.shape[0], -1)        # (N, S, 2, 3) -> I bits then Q bits


def llr_rtl(y, shift, mag_max):
    """integer LLRs of the RTL soft demapper (qam64_softdemap): received symbol in the PHY's 12-bit units (levels
    158 * {+-1, +-3, +-5, +-7}), per axis b_msb = y, b_mid = 4A - |y|, b_lsb = 2A - ||y| - 4A| (> 0: bit 1),
    magnitude min(|L| >> shift, mag_max).  Returned with the sign convention of llr() (> 0: bit 0)."""
    A = 158
    out = []
    for comp in (y.real, y.imag):
        yi = torch.round(comp * A * math.sqrt(42.0)).clamp(-2047, 2047)
        ay = yi.abs()
        ls = torch.stack([yi, 4 * A - ay, 2 * A - (ay - 4 * A).abs()], -1)
        mag = torch.floor(ls.abs() / 2 ** shift).clamp(max=mag_max)
        out.append(torch.where(ls > 0, -mag, mag))
    return torch.stack(out, -2).reshape(y.shape[0], -1)


# ---------------------------------------------------------------- LDPC (802.11n, from the Vivado IP code tables)
def wifi_h(vivado, rate):
    txt = (Path(vivado) / 'data/ip/xilinx/ldpc_v2_0/common_tcl/wifi_decode.yml').read_text()
    name = {'1/2': 'cr1_2', '2/3': 'cr2_3', '3/4': 'cr3_4', '5/6': 'cr5_6'}[rate]
    blk = txt.split(f'wifi802_11_{name}_1944_decode:')[1].split('wifi802_11_')[0]
    p, n, k = (int(re.search(rf'\b{x}: (\d+)', blk).group(1)) for x in ('p', 'n', 'k'))
    H = np.zeros((n - k, n), np.uint8)
    for r, c, s in re.findall(r'row: (\d+), col: (\d+), shift: (\d+)', blk):
        r, c, s = int(r), int(c), int(s)
        for i in range(p):
            H[r * p + i, c * p + (i + s) % p] = 1
    return H, n, k


def gf2_systematic(H):
    """generator for systematic encoding: information bits on the first k columns (802.11n codes have a
    full-rank dual-diagonal parity part), parity = A_inv B info"""
    m, n = H.shape; k = n - m
    A, B = H[:, k:].copy(), H[:, :k].copy()                   # H = [B | A]; A x_p + B x_i = 0
    M = np.concatenate([A, B], 1).astype(np.uint8)
    row = 0
    for col in range(m):
        piv = np.nonzero(M[row:, col])[0]
        assert len(piv), 'parity part not full rank'
        pr = row + piv[0]
        if pr != row:
            M[[row, pr]] = M[[pr, row]]
        others = np.nonzero(M[:, col])[0]
        others = others[others != row]
        M[others] ^= M[row]
        row += 1
    return M[:, m:]                                            # (m, k): parity = P @ info  (mod 2)


class LDPC:
    def __init__(self, vivado, rate):
        H, self.n, self.k = wifi_h(vivado, rate)
        self.P = torch.tensor(gf2_systematic(H), dtype=torch.float32, device=dev)
        Ht = torch.tensor(H, device=dev)
        m = H.shape[0]
        deg = H.sum(1); self.dmax = int(deg.max())
        idx = np.zeros((m, self.dmax), np.int64); mask = np.zeros((m, self.dmax), bool)
        for r in range(m):
            c = np.nonzero(H[r])[0]; idx[r, :len(c)] = c; mask[r, :len(c)] = True
        self.idx = torch.tensor(idx, device=dev); self.mask = torch.tensor(mask, device=dev)
        self.H = Ht

    def encode(self, info):                                    # (B, k) {0,1} float -> (B, n)
        par = torch.remainder(info @ self.P.T, 2)
        return torch.cat([info, par], 1)

    def decode(self, L):                                       # (B, n) channel LLR -> hard bits, ok flags
        B = L.shape[0]
        msg = torch.zeros(B, *self.idx.shape, device=dev)      # check -> variable
        mask = self.mask[None]
        for _ in range(ITER):
            tot = L.clone()
            tot.index_add_(1, self.idx[self.mask], msg[:, self.mask])   # variable totals
            v2c = tot[:, self.idx] - msg                       # (B, m, dmax)
            v2c = v2c.masked_fill(~mask, 1e9)
            sgn = torch.where(v2c < 0, -1.0, 1.0)
            prod = torch.prod(sgn, -1, keepdim=True)
            a = v2c.abs()
            m1, i1 = a.min(-1, keepdim=True)
            a2 = a.scatter(-1, i1, 1e9)
            m2 = a2.min(-1, keepdim=True).values
            mag = torch.where(torch.arange(self.dmax, device=dev)[None, None] == i1, m2, m1)
            msg = (0.75 * prod * sgn * mag).masked_fill(~mask, 0)
        tot = L.clone(); tot.index_add_(1, self.idx[self.mask], msg[:, self.mask])
        hard = (tot < 0).float()
        syn = torch.remainder(hard @ self.H.T.float(), 2).sum(1)
        return hard, syn == 0

    def decode_layered(self, L, iters, app_bits=None, msg_bits=None):
        """layered normalized min-sum (scale 0.75), one layer = the Z rows of a base-matrix row, stops at the first
        iteration whose hard decisions satisfy all checks.  app_bits / msg_bits None: floating point; otherwise the
        fixed-point decoder of the RTL: integer channel LLRs, v2c = sat_msg(APP - c2v_old),
        c2v = sign * (min - (min >> 2)), APP = sat_app(v2c + c2v).  Returns hard bits, ok flags, iterations used."""
        B, n = L.shape
        p = self.n // 24
        idx = torch.where(self.mask, self.idx, n)              # masked edges -> dummy column n
        app = torch.cat([L.float(), torch.zeros(B, 1, device=dev)], 1)
        msg = torch.zeros(B, *self.idx.shape, device=dev)
        hard = (L < 0).float(); ok = torch.zeros(B, dtype=torch.bool, device=dev)
        used = torch.full((B,), iters, device=dev)
        ar = torch.arange(self.dmax, device=dev)[None, None]
        Ht = self.H.T.float()
        for it in range(iters):
            for l in range(self.idx.shape[0] // p):
                rs = slice(l * p, (l + 1) * p)
                ix, mk = idx[rs], self.mask[rs][None]
                v = app[:, ix] - msg[:, rs]
                if msg_bits:
                    mx = 2 ** (msg_bits - 1) - 1
                    v = v.clamp(-mx, mx)
                sgn = torch.where(v < 0, -1.0, 1.0).masked_fill(~mk, 1.0)
                prod = torch.prod(sgn, -1, keepdim=True)
                a = v.abs().masked_fill(~mk, 1e9)
                m1, i1 = a.min(-1, keepdim=True)
                m2 = a.scatter(-1, i1, 1e9).min(-1, keepdim=True).values
                mag = torch.where(ar == i1, m2, m1)
                mag = mag - torch.floor(mag / 4) if msg_bits else 0.75 * mag
                new = (prod * sgn * mag).masked_fill(~mk, 0)
                a = v + new
                if app_bits:
                    mx = 2 ** (app_bits - 1) - 1
                    a = a.clamp(-mx, mx)
                app[:, ix] = a.masked_fill(~mk, 0)
                msg[:, rs] = new
            h = (app[:, :n] < 0).float()
            done = (torch.remainder(h @ Ht, 2).sum(1) == 0) & ~ok
            hard[done] = h[done]; used[done] = it + 1; ok |= done
            if ok.all():
                break
        hard[~ok] = (app[~ok, :n] < 0).float()
        return hard, ok, used


# ---------------------------------------------------------------- K=7 convolutional code (171, 133), soft Viterbi
G = (0o171, 0o133)


def cc_encode(bits):                                           # (B, L) -> (B, 2L), state = last 6 bits
    B, Lb = bits.shape
    b = bits.long()
    pad = torch.cat([torch.zeros(B, 6, dtype=torch.long, device=dev), b], 1)
    out = []
    for g in G:
        taps = [i for i in range(7) if (g >> (6 - i)) & 1]     # tap i: input delayed by i
        s = sum(pad[:, 6 - t: 6 - t + Lb] for t in taps)
        out.append(s % 2)
    return torch.stack(out, -1).reshape(B, -1).float()


def cc_viterbi(L):                                             # (B, 2N) LLR -> (B, N) bits; starts / ends in 0
    B = L.shape[0]; N = L.shape[1] // 2
    Lr = L.view(B, N, 2)
    ns = 64
    st = torch.arange(ns, device=dev)
    outs = torch.zeros(ns, 2, 2, device=dev)                  # expected bits (state, input, branch)
    nxt = torch.zeros(ns, 2, dtype=torch.long, device=dev)
    for s in range(ns):
        for u in range(2):
            reg = (u << 6) | s                                 # newest bit on top
            for j, g in enumerate(G):
                outs[s, u, j] = bin(reg & g).count('1') % 2
            nxt[s, u] = reg >> 1
    # predecessor table: for next state t, (s0, u0), (s1, u1)
    pred = [[] for _ in range(ns)]
    for s in range(ns):
        for u in range(2):
            pred[nxt[s, u].item()].append((s, u))
    ps = torch.tensor([[p[0][0], p[1][0]] for p in pred], device=dev)
    pu = torch.tensor([[p[0][1], p[1][1]] for p in pred], device=dev)
    sgn = 1 - 2 * outs[ps, pu]                                 # (64, 2 pred, 2 bits): +1 for bit 0
    metric = torch.full((B, ns), -1e9, device=dev); metric[:, 0] = 0
    dec = torch.zeros(N, B, ns, dtype=torch.uint8, device=dev)
    for t in range(N):
        bm = (Lr[:, t, None, None, :] * sgn[None]).sum(-1) / 2   # (B, 64, 2)
        cand = metric[:, ps] + bm
        best, arg = cand.max(-1)
        dec[t] = arg.to(torch.uint8)
        metric = best
    s = torch.zeros(B, dtype=torch.long, device=dev)          # terminated in state 0
    bits = torch.zeros(B, N, device=dev)
    for t in range(N - 1, -1, -1):
        a = dec[t].gather(1, s[:, None]).squeeze(1).long()
        bits[:, t] = pu[s, a].float()
        s = ps[s, a]
    return bits


# ---------------------------------------------------------------- source
def jpeg_fit(img, max_bytes):
    lo, hi, best = 1, 100, None
    while lo <= hi:
        q = (lo + hi) // 2
        buf = io.BytesIO(); Image.fromarray(img).save(buf, 'JPEG', quality=q, subsampling=2)
        if buf.tell() <= max_bytes:
            best, lo = (buf.getvalue(), q), q + 1
        else:
            hi = q - 1
    return best


def jpeg_decode(b, shape):
    try:
        im = Image.open(io.BytesIO(b)); im.load()
        a = np.asarray(im.convert('RGB'))
        return a if a.shape == shape else None
    except Exception:
        return None


def to_bits(byts, nbits):
    a = np.unpackbits(np.frombuffer(byts, np.uint8))
    out = np.zeros(nbits, np.uint8); out[:min(len(a), nbits)] = a[:nbits]
    return out


def from_bits(bits):
    return np.packbits(bits.astype(np.uint8)).tobytes()


# ---------------------------------------------------------------- simulation
def run(code, imgs, snrs, vivado, seed=1):
    rng = np.random.default_rng(seed)
    if code == 'cc':
        info_bits = FRAME_BITS // 2 - 128
        enc = None
    else:
        enc = LDPC(vivado, code[4:])
        ncw = FRAME_BITS // enc.n
        info_bits = ncw * enc.k
    pay = info_bits // 8
    scr = rng.integers(0, 2, info_bits).astype(np.uint8)       # PRBS scrambler (fixed)
    pk, qs = [], []
    for im in imgs:
        jb, q = jpeg_fit(im, pay - HDR)
        pk.append(to_bits(bytes(HDR) + jb, info_bits) ^ scr); qs.append(q)
    info = torch.tensor(np.stack(pk), dtype=torch.float32, device=dev)
    B = info.shape[0]
    if code == 'cc':
        coded = cc_encode(torch.cat([info, torch.zeros(B, 128, device=dev)], 1))
    else:
        cw = enc.encode(info.view(B * ncw, enc.k)).view(B, -1)
        coded = torch.cat([cw, torch.zeros(B, FRAME_BITS - cw.shape[1], device=dev)], 1)
    x = modulate(coded)
    ref = np.stack(imgs).astype(np.float64)
    res = {}
    gen = torch.Generator(device=dev).manual_seed(seed)
    for snr in snrs:
        t0 = time.time()
        nz = (torch.randn(x.shape, generator=gen, device=dev) + 1j * torch.randn(x.shape, generator=gen, device=dev)) \
            * math.sqrt(10 ** (-snr / 10) / 2)
        L = llr(x + nz.to(x.dtype), snr)
        if code == 'cc':
            hat = cc_viterbi(L)[:, :info_bits]
            frame_ok = (hat == info).all(1)
        else:
            hb, ok = enc.decode(L[:, :ncw * enc.n].reshape(B * ncw, enc.n))
            hat = hb.view(B, ncw, enc.n)[:, :, :enc.k].reshape(B, -1)
            frame_ok = ok.view(B, ncw).all(1)
        hat = hat.cpu().numpy().astype(np.uint8) ^ scr
        se, dec_ok = 0.0, 0
        for i in range(B):
            byts = from_bits(hat[i])
            out = jpeg_decode(byts[HDR:], imgs[i].shape)
            if out is None:
                out = np.full(imgs[i].shape, 128, np.uint8)
            else:
                dec_ok += 1
            se += ((out.astype(np.float64) - ref[i]) ** 2).sum()
        psnr = 10 * math.log10(255 ** 2 / (se / ref.size))
        res[f'{snr:g}'] = dict(psnr=round(psnr, 4), frame_ok=round(float(frame_ok.float().mean()), 4),
                               jpeg_decodable=round(dec_ok / B, 4))
        print(f'{code:9s} {snr:5.1f} dB: PSNR {psnr:6.2f}  error-free frames {frame_ok.float().mean():.3f}  '
              f'JPEG decodable {dec_ok / B:.2f}  ({time.time() - t0:.1f} s)', flush=True)
    return dict(payload_bytes=pay, jpeg_quality_mean=float(np.mean(qs)), psnr_db=res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', required=True)
    ap.add_argument('--vivado', default='D:/AMDDesignTools/2025.2/Vivado')
    ap.add_argument('--out', required=True)
    ap.add_argument('--n', type=int, default=100)
    ap.add_argument('--codes', default='ldpc1/2,ldpc2/3,ldpc3/4,ldpc5/6,cc')
    ap.add_argument('--snr', default='0:31:1')
    a = ap.parse_args()
    imgs = list(np.load(Path(a.cache) / 'val_center256.npy')[:a.n].astype(np.uint8))
    lo, hi, st = (float(v) for v in a.snr.split(':'))
    snrs = list(np.arange(lo, hi, st))
    out = {}
    if Path(a.out).exists():
        out = json.load(open(a.out, encoding='utf-8'))
    for code in a.codes.split(','):
        out[code] = run(code, imgs, snrs, a.vivado)
        Path(a.out).write_text(json.dumps(out, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
