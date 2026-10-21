"""Diagnostic (read only, jscc-rx and the GUI stopped: uses the capture DMA, and the AGC watchdog of rx_server is off):
raw ADC captures at the low-SNR edge of the link, for an offline run of the RX synchronisation model, with the PHY
sync rate (SYNC_CNT) and the AD9361 gain index at each attenuation, plus captures with the TX attenuation at maximum
(noise floor at the same gain).

    bash -lc 'source /etc/profile.d/pynq_venv.sh && python3 adc_edge.py --att 48,46,45,44,43,42,40 --out meas/adc_edge'
-> <out>.npz: att (k,), sync_rate (k,) frames/s, gain (k, n), raw (k, n, len, 2) int16 I/Q,
              raw_off (n, len, 2), gain_off (n,)
"""
import argparse
import socket
import time
import numpy as np
from jscc_rx import JsccRx
import jscc_udp as U

ap = argparse.ArgumentParser()
ap.add_argument("--att", default="48,46,45,44,43,42,40", help="extra TX attenuation points, descending (GUI scale)")
ap.add_argument("--top", type=float, default=50.0, help="extra dB to settle at before the sweep")
ap.add_argument("--att0", type=float, default=0.0)
ap.add_argument("--src", default="preset:div2k_0802")
ap.add_argument("--mode", default="jscc")
ap.add_argument("--n", type=int, default=3, help="captures per point")
ap.add_argument("--len", type=int, default=2_000_000, help="samples per capture (20 MSPS)")
ap.add_argument("--rate-s", type=float, default=4.0, help="s to count SYNC_CNT at each point")
ap.add_argument("--off-att", type=float, default=89.75, help="TX attenuation for the noise-floor captures")
ap.add_argument("--out", default="meas/adc_edge")
args = ap.parse_args()

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(1.0)
TX = ("192.168.4.1", U.PORT_TX)


def tx(cmd):
    """send a command, return the ATTS answer (preview image chunks after a HELLO are skipped)"""
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
        x = rx.capture_adc(args.len, trig="now", timeout=2.0)
        gain.append(rx.rd("AGC") & 0x7F)
        raw.append(np.stack([x.real, x.imag], -1).astype(np.int16))
        time.sleep(0.05)
    print(f"{tag}: gain {gain}, CAP_OVF {rx.rd('CAP_OVF')}", flush=True)
    return np.stack(raw), np.array(gain)


rx = JsccRx(download=False)
orig = tx(U.HELLO)
print("TX at start:", orig, flush=True)
atts = [float(a) for a in args.att.split(",")]
out = dict(att=np.array(atts), top=args.top, att0=args.att0, src=args.src, mode=args.mode, len=args.len)
rates, gains, raws = [], [], []
try:
    print(tx(U.SRC + b" " + args.src.encode()), flush=True)
    print(tx(U.MODE + b" " + args.mode.encode()), flush=True)
    tx(U.ATTN + b" %.2f" % (args.att0 + args.top)); time.sleep((args.top + 2) / 10 + 5)      # up at 10 dB/s
    for att in atts:
        print(tx(U.ATTN + b" %.2f" % (args.att0 + att)), flush=True); time.sleep(4)          # down: 10 dB/s
        c0, t0 = rx.rd("SYNC_CNT"), time.time()
        time.sleep(args.rate_s)
        rate = ((rx.rd("SYNC_CNT") - c0) & 0xFFFFFFFF) / (time.time() - t0)
        print(f"att {att}: SYNC_CNT {rate:.1f} /s", flush=True)
        r, g = grab(f"att {att}")
        rates.append(rate); gains.append(g); raws.append(r)
    out.update(sync_rate=np.array(rates), gain=np.stack(gains), raw=np.stack(raws))
    tx(U.ATTN + b" %.2f 0" % args.off_att); time.sleep(4)
    out["raw_off"], out["gain_off"] = grab("TX attenuation max")
finally:
    f = orig.split() if orig else []
    if len(f) >= 5:                                   # JSCC-ATTS <db> <target> <mode> <src>
        tx(U.MODE + b" " + f[3].encode()); tx(U.SRC + b" " + f[4].encode()); tx(U.ATTN + b" " + f[2].encode())
    print("TX restored:", tx(U.HELLO), flush=True)
np.savez_compressed(args.out + ".npz", **out)
print("saved", args.out + ".npz", flush=True)
