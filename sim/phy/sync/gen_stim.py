"""stimulus for tb_sync: frames = gap + preamble + NSYM random 64-QAM OFDM symbols, random half-sample timing,
random CFO (+-36.6 kHz), AWGN at SNR; 12-bit I/Q at ~ -14 dBFS. Writes stim_<tag>.txt (one hex {Q,I} per line) and
truth_<tag>.txt (frame, input index of the first LTF1 sample, frac, cfo_hz).

    python gen_stim.py <snr_db> <frames> <tag> [seed]      e.g. python gen_stim.py 4 60 s4
"""
import os
import sys
import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))
snr, nfr, tag = float(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
seed = int(sys.argv[4]) if len(sys.argv) > 4 else 1
rng = np.random.default_rng(seed)
NSYM = 20
FS = 20e6


def s12(x):
    return np.where(x > 2047, x - 4096, x)


v = np.array([int(l, 16) for l in open(ROOT + '/src/hw/tx/rtl/phy/ofdm_tx/preamble.mem').read().split()])
pre = (s12(v & 0xFFF) + 1j * s12(v >> 12)) / 1024.0
used = [k for k in range(64) if not (k <= 5 or k >= 59 or k == 32)]
lv = np.array([-7, -5, -3, -1, 1, 3, 5, 7]) / np.sqrt(42)


def ofdm(n):
    out = []
    for _ in range(n):
        X = np.zeros(64, complex)
        X[used] = rng.choice(lv, len(used)) + 1j * rng.choice(lv, len(used))
        x = np.fft.ifft(np.fft.ifftshift(X)) * 8
        out.append(np.r_[x[-16:], x])
    return np.concatenate(out)


GAP = 600
segs, truth, pos = [], [], 0
for f in range(nfr):
    tx = np.r_[np.zeros(GAP, complex), pre, ofdm(NSYM), np.zeros(200, complex)]
    frac, cfo = rng.random(), rng.uniform(-36.6e3, 36.6e3)
    n = len(tx)
    fr = np.fft.fftfreq(n)
    y = np.fft.ifft(np.fft.fft(tx) * np.exp(-2j * np.pi * fr * frac))
    y = y * np.exp(2j * np.pi * cfo / FS * np.arange(n) + 2j * np.pi * rng.random())
    segs.append(y)
    truth.append((f, pos + GAP + 192, frac, cfo))
    pos += n
y = np.concatenate(segs)
p_sig = np.mean(abs(np.concatenate([s[GAP + 320:GAP + 320 + 80 * NSYM] for s in segs])) ** 2)
y = y + np.sqrt(p_sig / 10 ** (snr / 10) / 2) * (rng.standard_normal(len(y)) + 1j * rng.standard_normal(len(y)))
scale = 2048 * 10 ** (-14 / 20) / np.sqrt(p_sig)          # data rms at -14 dBFS (AGC lock level)
q = np.clip(np.round(y.real * scale), -2048, 2047) + 1j * np.clip(np.round(y.imag * scale), -2048, 2047)
with open(f'stim_{tag}.txt', 'w') as fh:
    for i, qq in zip(q.real.astype(int), q.imag.astype(int)):
        fh.write(f'{((qq & 0xFFF) << 12) | (i & 0xFFF):06x}\n')
with open(f'truth_{tag}.txt', 'w') as fh:
    for t in truth:
        fh.write(f'{t[0]} {t[1]} {t[2]:.4f} {t[3]:.1f}\n')
print(len(q), 'samples')
