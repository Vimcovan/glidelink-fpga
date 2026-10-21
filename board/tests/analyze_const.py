"""Data-aided constellation SNR for measure_link.py runs with a fixed TX image (--src preset:..., --save-const).

With a still image every frame carries the same symbols (DeepJSCC: deterministic encoder, PN scrambler restarted
per frame; SSCC: scrambler restarted per frame, only the packet header -
frame number, CRC - changes). Reference = 64-QAM decision of the mean of all snapshots at the strongest point
(lowest attenuation, EVM ~ -34 dB: no decision errors); positions whose decision differs between snapshots there
are excluded, and for SSCC the first HDR_ROWS OFDM symbols (packet header: frame number / CRC, and the parity of
its LDPC codeword). Snapshots of a frame with other content (rare TX frames with different SSCC packet bytes, false syncs:
correlation with the reference rho ~ 0.03) are excluded by rho < RHO_MIN (same content: rho = sqrt(snr/(1+snr)),
0.71 at 0 dB) and counted (excl). Per point: SNR = mean |ref|^2 / mean |rx - ref|^2 over the kept snapshots.

    python analyze_const.py meas/k0802_v2.json meas/k0802_v2_const  -> meas/k0802_v2_da.json (+ snr_da_db per point)
"""
import json
import os
import sys
import numpy as np

QAM = np.array([-1106, -790, -474, -158, 158, 474, 790, 1106])
NULLS = set(range(0, 6)) | {32} | set(range(59, 64)); PILOTS = [11, 25, 39, 53]
DATA = [k for k in range(64) if k not in NULLS and k not in PILOTS]
NSYM = 683
HDR_ROWS = {"sscc": 7}                            # OFDM symbols covering the first LDPC codeword (324 symbols),
                                                  # which carries the SSCC packet header
RHO_MIN = 0.5


def decide(z):
    return QAM[np.argmin(abs(z.real[..., None] - QAM), -1)] + 1j * QAM[np.argmin(abs(z.imag[..., None] - QAM), -1)]


def load(cdir, fn):
    d = np.load(os.path.join(cdir, fn))
    return d["start_sym"], d["cpe"][:, :, DATA]


src, cdir = sys.argv[1], sys.argv[2]
out = sys.argv[3] if len(sys.argv) > 3 else src.rsplit(".", 1)[0] + "_da.json"
J = json.load(open(src)); R = J["results"]
refs = {}
for mode in sorted({r["mode"] for r in R}):
    pts = [r for r in R if r["mode"] == mode and r.get("const")]
    r0 = min(pts, key=lambda r: r["att"])
    s, Z = load(cdir, r0["const"])
    ref = {}
    for st in sorted(set(s.tolist())):
        z = Z[s == st]
        rf = decide(z.mean(0))
        mask = (decide(z) == rf[None]).all(0)              # positions identical in every frame
        if st + 19 == NSYM - 1:
            mask[-1, -16:] = False                         # zero padding of the last OFDM symbol
        mask[:max(0, HDR_ROWS.get(mode, 0) - st)] = False
        ref[st] = (rf, mask)
        print(f"{mode}: reference from +{r0['att']} dB, start_sym {st}, {len(z)} snapshots, "
              f"{mask.mean() * 100:.1f} % of positions fixed")
    refs[mode] = ref
for r in R:
    if not r.get("const"):
        continue
    s, Z = load(cdir, r["const"])
    num = den = 0.0; n_all = n_ex = 0
    for st, (rf, mask) in refs[r["mode"]].items():
        z = Z[s == st][:, mask]
        if not len(z):
            continue
        ref = rf[mask]
        rho = abs(z @ ref.conj()) / np.linalg.norm(z, axis=1) / np.linalg.norm(ref)
        keep = z[rho >= RHO_MIN]
        n_all += len(z); n_ex += len(z) - len(keep)
        num += len(keep) * float(np.mean(abs(ref) ** 2))
        den += float(np.sum(np.mean(abs(keep - ref) ** 2, 1)))
    r["snr_da_db"] = round(10 * np.log10(num / den), 2) if den > 0 else None
    r["snr_da_excl"] = round(n_ex / n_all, 3) if n_all else None
    print(f"{r['mode']} +{r['att']:5.1f} dB: data-aided SNR {r['snr_da_db']}, -EVM(decision) {-r['evm_db']:.2f}, "
          f"PSNR {r['psnr_shown']}, excluded {r['snr_da_excl']}")
J["data_aided"] = dict(method=__doc__.split("\n\n")[1], const_dir=cdir)
json.dump(J, open(out, "w"), indent=1)
print("written", out)
