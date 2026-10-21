"""Visual comparison grid (Original | DeepJSCC-Q | SSCC) at several over-the-air channel conditions, from a
measure_link.py --save-img run (one decoded image per point: the frame whose PSNR is closest to the point mean).
Rows: TX attenuation points (same for both schemes); row label: constellation SNR of the point (data-aided,
analyze_const.py; mean of the two schemes' passes, leaving out estimates with >= 5 % false-sync snapshots). Under each image: PSNR / SSIM against the TX image.
SSIM: Wang et al. 2004 (11x11 Gaussian window, sigma 1.5, K1 0.01, K2 0.03, L 255), mean over R, G, B.
A point where no SSCC frame could be decoded shows the mid-gray image the receiver displays.

    python plot_visual_compare.py meas/k0802_v3_da.json meas/k0802_v3_img meas/k0802_v3_visual 0,12,18.5,20,24,32 [h]
    last argument h: horizontal layout (rows = Original / DeepJSCC-Q / SSCC, columns = SNR), e.g. for a poster
"""
import json
import os
import sys
import numpy as np
from PIL import Image
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def ssim(a, b):
    a, b = a.astype(np.float64), b.astype(np.float64)
    r = np.arange(-5, 6); g = np.exp(-r ** 2 / (2 * 1.5 ** 2)); g /= g.sum()
    blur = lambda x: np.apply_along_axis(lambda v: np.convolve(v, g, "valid"), 0,
                                         np.apply_along_axis(lambda v: np.convolve(v, g, "valid"), 1, x))
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    out = []
    for ch in range(3):
        x, y = a[..., ch], b[..., ch]
        mx, my = blur(x), blur(y)
        sxx, syy, sxy = blur(x * x) - mx ** 2, blur(y * y) - my ** 2, blur(x * y) - mx * my
        out.append(np.mean((2 * mx * my + c1) * (2 * sxy + c2) / ((mx ** 2 + my ** 2 + c1) * (sxx + syy + c2))))
    return float(np.mean(out))


def psnr(a, b):
    return float(10 * np.log10(255 ** 2 / np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)))


js, imgdir, out = sys.argv[1], sys.argv[2], sys.argv[3]
atts = [float(x) for x in sys.argv[4].split(",")]
H = len(sys.argv) > 5 and sys.argv[5] == "h"
R = {(r["mode"], r["att"]): r for r in json.load(open(js))["results"]}
ref = np.asarray(Image.open(os.path.join(imgdir, "tx_reference.png")).convert("RGB"))
gray = np.full_like(ref, 128)

plt.rcParams.update({"font.family": "serif", "font.serif": ["Times New Roman", "DejaVu Serif"], "font.size": 11})
names = ("Original", "DeepJSCC-Q", "SSCC (JPEG + LDPC)")
n = len(atts)
fig, axs = plt.subplots(3, n, figsize=(2.0 * n, 6.75), dpi=200) if H else plt.subplots(n, 3, figsize=(6.3, 2.18 * n), dpi=200)
cell = (lambda i, j: axs[j, i]) if H else (lambda i, j: axs[i, j])        # i: SNR point, j: scheme
for i, att in enumerate(atts):
    # SNR estimates with >= 5 % of the snapshots from false syncs are unreliable (same rule as the PSNR-SNR plot)
    snrs = [R[(m, att)]["snr_da_db"] for m in ("jscc", "sscc") if R.get((m, att), {}).get("snr_da_db") is not None
            and (R[(m, att)].get("snr_da_excl") or 0) < 0.05]
    for j, mode in enumerate((None, "jscc", "sscc")):
        ax = cell(i, j)
        if mode is None:
            im, cap = ref, "PSNR / SSIM"
        else:
            r = R[(mode, att)]
            fn = r.get("img")
            if fn:
                im = np.asarray(Image.open(os.path.join(imgdir, fn)).convert("RGB"))
                cap = f"{psnr(im, ref):.2f} dB / {ssim(im, ref):.2f}"
            else:
                im = gray
                cap = f"{psnr(im, ref):.2f} dB / {ssim(im, ref):.2f}" + (" (N/A)" if H else " (not decodable)")
            print(f"att {att:5.1f} {mode}: {cap}  (SNR {r.get('snr_da_db')} dB, CRC {r.get('crc_ok')})")
        ax.imshow(im); ax.set_xticks([]); ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.set_xlabel(cap, fontsize=10.5, labelpad=3)
        snr_txt = f"SNR {np.mean(snrs):.1f} dB"
        if H:
            if j == 0:
                ax.set_title(snr_txt, fontweight="bold", fontsize=12, pad=6)
            if i == 0:
                ax.set_ylabel(names[j], fontweight="bold", fontsize=12, labelpad=6)
        else:
            if i == 0:
                ax.set_title(names[j], fontweight="bold", fontsize=12, pad=6)
            if j == 0:
                ax.set_ylabel(snr_txt, fontsize=11, labelpad=6)
fig.tight_layout(h_pad=0.6, w_pad=0.4)
for ext in ("pdf", "png"):
    fig.savefig(f"{out}.{ext}", facecolor="white")
print("written", out + ".{pdf,png}")
