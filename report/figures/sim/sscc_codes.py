"""Choice of the SSCC channel code: PSNR vs SNR on AWGN (src/model/sscc_sim.py, DIV2K validation 100 crops, the frame
of the system: 32,768 64-QAM symbols per image) for JPEG + K=7 convolutional code 1/2 and JPEG + 802.11n LDPC
(n = 1944) at the four rates, with the W8A12 DeepJSCC-Q model for reference.

    python sscc_codes.py      -> ../hw/sscc_codes.png
"""
import json
import os

import matplotlib.pyplot as plt

import plotstyle as S

HERE = os.path.dirname(os.path.abspath(__file__))
NOTES = os.path.normpath(os.path.join(HERE, "..", "..", "notes", "figures"))
S.apply()
ss = json.load(open(os.path.join(NOTES, "sscc_ldpc", "sscc_sim.json"), encoding="utf-8"))
dj = json.load(open(os.path.join(NOTES, "shallow_decoder", "int_sweep.json"), encoding="utf-8"))["psnr_db"]

fig, ax = plt.subplots(figsize=(5.6, 3.1))
x = sorted(float(k) for k in dj if k[0].isdigit())
ax.plot(x, [dj[f"{v:g}"]["int"] for v in x], color=S.BLUE, lw=1.5, marker="o", ms=3, mfc="white", label="DeepJSCC-Q（W8A12）")
for code, col, lab in (("cc", S.GRAY, "卷积码 1/2"), ("ldpc1/2", "#d9a441", "LDPC 1/2"), ("ldpc2/3", S.ORANGE, "LDPC 2/3"),
                       ("ldpc3/4", "#8064a2", "LDPC 3/4"), ("ldpc5/6", "#4f8f3a", "LDPC 5/6")):
    p = ss[code]["psnr_db"]
    xs = sorted(float(k) for k in p)
    ax.plot(xs, [p[f"{v:g}"]["psnr"] for v in xs], color=col, lw=1.6 if code == "ldpc2/3" else 1.0,
            label=f"JPEG + {lab}（{ss[code]['payload_bytes'] // 4 * 4:,} B）")
ax.set_xlim(0, 30); ax.set_ylim(10, 34.5)
ax.set_xlabel("SNR / dB（AWGN）"); ax.set_ylabel("PSNR / dB")
ax.legend(loc="lower right", fontsize=7, handlelength=1.6)
S.finish(ax)
S.save(fig, os.path.join(HERE, "..", "hw", "sscc_codes.png"))
