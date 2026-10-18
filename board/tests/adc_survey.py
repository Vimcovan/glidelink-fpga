"""Diagnostic (read only, jscc-rx stopped: uses the capture DMA): raw ADC captures with the link running and with the
TX attenuation at maximum (only what else is on the channel), with the AD9361 gain index of each capture, to see
what interferes on the carrier.

    bash -lc 'source /etc/profile.d/pynq_venv.sh && python3 adc_survey.py --n 8 --out meas/adc_survey'
-> <out>.npz: raw_on / raw_off (n, len, 2) int16 I/Q, gain_on / gain_off (n,) gain index, lo_mhz
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
ap.add_argument("--n", type=int, default=8, help="captures per condition")
ap.add_argument("--len", type=int, default=2_000_000, help="samples per capture (20 MSPS)")
ap.add_argument("--off-att", type=float, default=89.75, help="TX attenuation for the 'link off' captures")
ap.add_argument("--out", default="meas/adc_survey")
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


def grab(tag):
    raw, gain = [], []
    for _ in range(args.n):
        gain.append(rx.rd("AGC") & 0x7F)
        x = rx.capture_adc(args.len, trig="now", timeout=2.0)
        raw.append(np.stack([x.real, x.imag], -1).astype(np.int16))
        time.sleep(0.2)
    print(f"{tag}: {args.n} captures, gain {gain}, CAP_OVF {rx.rd('CAP_OVF')}", flush=True)
    return np.stack(raw), np.array(gain)


rx = JsccRx()                                     # fresh bitstream + AD9361 init (DMAs restarted)
time.sleep(3)
orig = tx(U.HELLO)
print("TX at start:", orig, flush=True)
out = dict(lo_mhz=ad9361_lo.configured_mhz(os.path.dirname(os.path.abspath(__file__))))
try:
    out["raw_on"], out["gain_on"] = grab("link on")
    print(tx(U.ATTN + b" %.2f 0" % args.off_att), flush=True); time.sleep(4)
    out["raw_off"], out["gain_off"] = grab("TX attenuation max")
finally:
    f = orig.split() if orig else []
    if len(f) >= 3:                                   # JSCC-ATTS <db> <target> ...
        tx(U.ATTN + b" " + f[2].encode() + b" 0")
    print("TX restored:", tx(U.HELLO), flush=True)
os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
np.savez_compressed(args.out + ".npz", **out)
print("saved", args.out + ".npz", flush=True)
