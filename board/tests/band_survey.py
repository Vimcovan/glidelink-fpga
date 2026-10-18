"""Diagnostic (jscc-rx stopped: uses the capture DMA and retunes the RX board's AD9361): occupancy of the 2.4 GHz band.
The TX is attenuated to the maximum; the RX AD9361 is set to a fixed (manual) gain and stepped over carriers; at each
carrier N raw captures are cut into 256-point spectra (12.8 us) and, per 78 kHz bin, the fraction of spectra more
than THR dB above the bin's noise floor is counted. jscc-rx re-initialises the AD9361 when it is started again.

    bash -lc 'source /etc/profile.d/pynq_venv.sh && python3 band_survey.py --out meas/band_survey'
-> <out>.npz: lo_mhz (k,), freq_mhz (k, 256), occ (k, 256), p_mean / p_p90 (k, 256) dBFS per bin, floor (k, 256)
"""
import argparse
import os
import socket
import time
import numpy as np
from jscc_rx import JsccRx
import jscc_udp as U
import ad9361_lo

ap = argparse.ArgumentParser()
ap.add_argument("--start", type=float, default=2412.0)
ap.add_argument("--stop", type=float, default=2472.0)
ap.add_argument("--step", type=float, default=10.0)
ap.add_argument("--n", type=int, default=5, help="captures per carrier")
ap.add_argument("--len", type=int, default=2_000_000)
ap.add_argument("--gain", type=int, default=30, help="manual full-table gain index")
ap.add_argument("--thr", type=float, default=10.0, help="dB above the bin noise floor that counts as occupied")
ap.add_argument("--out", default="meas/band_survey")
args = ap.parse_args()

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(1.0)
TX = ("192.168.4.1", U.PORT_TX)


def tx(cmd):
    s.sendto(cmd, TX)
    t_end = time.time() + 1.0
    while time.time() < t_end:
        try:
            d = s.recv(65536)
        except socket.timeout:
            break
        if d.startswith(U.ATTS):
            return d.decode()
    return None


rx = JsccRx()
rf = rx.rf
spi = lambda a, v=None: rf.rd(a) if v is None else rf.wr(a, v)
orig = tx(U.HELLO)
print("TX at start:", orig, flush=True)
nf = 256
win = np.hanning(nf)
res = {k: [] for k in ("lo_mhz", "freq_mhz", "occ", "p_mean", "p_p90", "floor")}
try:
    tx(U.ATTN + b" 89.75 0")
    t_end = time.time() + 15                                     # the GUI attenuation ramps at 10 dB/s
    while time.time() < t_end and not (orig and (tx(U.HELLO) or "").split()[1:2] == ["89.75"]):
        time.sleep(1)
    print("TX:", tx(U.HELLO), flush=True)
    spi(0x0FA, spi(0x0FA) & ~0x03)                               # RX1 gain control: manual
    spi(0x109, args.gain)
    f = args.start
    while f <= args.stop + 1e-6:
        ad9361_lo.retune(spi, f * 1e6, log=lambda m: None)
        spi(0x109, args.gain)
        time.sleep(0.2)
        P = []
        for _ in range(args.n):
            x = rx.capture_adc(args.len, trig="now", timeout=2.0)
            m = len(x) // nf
            S = np.fft.fftshift(np.fft.fft(x[:m * nf].reshape(m, nf) * win, axis=1), axes=1)
            P.append(10 * np.log10(np.abs(S) ** 2 / (2048.0 ** 2 * (win ** 2).sum()) + 1e-15))
        P = np.concatenate(P)
        floor = np.percentile(P, 10, axis=0)
        occ = (P > floor + args.thr).mean(0)
        res["lo_mhz"].append(f)
        res["freq_mhz"].append(f + np.fft.fftshift(np.fft.fftfreq(nf, 1 / 20.0)))
        res["occ"].append(occ); res["floor"].append(floor)
        res["p_mean"].append(10 * np.log10((10 ** (P / 10)).mean(0))); res["p_p90"].append(np.percentile(P, 90, axis=0))
        band = np.abs(res["freq_mhz"][-1] - f) <= 8.1
        print(f"{f:7.1f} MHz: occupied {100 * occ[band].mean():5.1f} % of time (in +-8.1 MHz, >{args.thr:g} dB above floor), "
              f"floor {np.median(floor):.1f} dBFS, gain {spi(0x0B0) & 0x7F}", flush=True)
        f += args.step
finally:
    fld = orig.split() if orig else []
    if len(fld) >= 3:
        tx(U.ATTN + b" " + fld[2].encode() + b" 0")
    print("TX restored (ramping):", tx(U.HELLO), flush=True)
os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
np.savez_compressed(args.out + ".npz", **{k: np.array(v) for k, v in res.items()}, gain=args.gain, thr=args.thr)
print("saved", args.out + ".npz", flush=True)
