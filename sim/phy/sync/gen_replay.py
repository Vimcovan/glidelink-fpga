"""tb_sync stimulus from the board ADC captures (adc_edge.py): for each attenuation the frames found by an ideal
preamble matched filter, each cut as [start - 1500, start + 2500); then noise-only samples. Writes stim_<tag>.txt and
truth_<tag>.txt (frame, input index of the first LTF1 sample, att, 0).

    python gen_replay.py <adc_edge.npz> <tag> <att list>     e.g. python gen_replay.py adc_edge.npz board 48,46,45,44
"""
import os, sys
import numpy as np
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))
d = np.load(sys.argv[1]); tag = sys.argv[2]; atts = [float(a) for a in sys.argv[3].split(',')]
def s12(v): return np.where(v > 2047, v - 4096, v)
v = np.array([int(l, 16) for l in open(ROOT + '/src/hw/tx/rtl/phy/ofdm_tx/preamble.mem').read().split()])
pre = s12(v & 0xFFF) + 1j * s12(v >> 12)
stim, truth = [], []
n = 0
for a in atts:
    k = list(d['att']).index(a)
    for r in d['raw'][k]:
        c = r[:, 0].astype(float) + 1j * r[:, 1]
        mf = abs(np.convolve(c, np.conj(pre[::-1]), 'valid')) ** 2
        thr = 0.25 * mf.max(); idx = []
        for i in np.argsort(mf)[::-1][:200]:
            if mf[i] < thr: break
            if all(abs(i - j) > 20000 for j in idx): idx.append(i)
        for s0 in sorted(idx):
            if s0 < 1500 or s0 + 2500 > len(r): continue
            seg = r[s0 - 1500: s0 + 2500]
            truth.append(f"{len(truth)} {n + 1500 + 192} {a} 0")
            stim.append(seg); n += len(seg)
noise = d['raw_off'][0][:50000]
stim.append(noise); n += len(noise)
x = np.concatenate(stim).astype(np.int64)
words = ((x[:, 1] & 0xFFF) << 12) | (x[:, 0] & 0xFFF)
open(f'stim_{tag}.txt', 'w').write('\n'.join(f'{w:06x}' for w in words) + '\n')
open(f'truth_{tag}.txt', 'w').write('\n'.join(truth) + '\n')
print(f'{len(truth)} frames, {n} samples (noise from {n - len(noise)})')
