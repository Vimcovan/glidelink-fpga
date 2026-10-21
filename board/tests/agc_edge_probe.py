"""Diagnostic (jscc-rx keeps running, so the AGC watchdog is active): PHY sync rate and AD9361 gain index vs TX
attenuation for several values of one fast-AGC register, e.g. 0x110 (D0 = allow gain increase below the low power
threshold 0x114, D7 = also after gain lock) or 0x118 (max gain index increase for the lock level adjustment).
The register is written live and restored at the end.

    bash -lc 'source /etc/profile.d/pynq_venv.sh && python3 agc_edge_probe.py --reg 0x110 --vals 0x48,0x49,0xC9'
"""
import argparse
import socket
import time
import numpy as np
from pynq import MMIO
from ad9361_ps import AD9361
import jscc_udp as U

ap = argparse.ArgumentParser()
ap.add_argument("--reg", default="0x110")
ap.add_argument("--vals", default="0x48,0x49,0xC9")
ap.add_argument("--att", default="50,48,47,46,45,44,43,42,40,30,20,10,0", help="descending TX attenuation points")
ap.add_argument("--top", type=float, default=52.0)
ap.add_argument("--src", default="preset:div2k_0802")
ap.add_argument("--settle", type=float, default=5.0)
ap.add_argument("--count", type=float, default=5.0, help="s to count SYNC_CNT / IMG_FRAMES")
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


m = MMIO(0x8002_0000, 0x1_0000); rf = AD9361(m)
SYNC, IMG, DROP, AGC = 0x20, 0x34, 0x2C, 0x3C
REG = int(args.reg, 0)
orig_val = rf.rd(REG)
orig = tx(U.HELLO)
print(f"0x{REG:03X} at start 0x{orig_val:02X}; TX at start: {orig}", flush=True)
atts = [float(a) for a in args.att.split(",")]
try:
    print(tx(U.SRC + b" " + args.src.encode()), tx(U.MODE + b" jscc"), flush=True)
    for v in [int(x, 0) for x in args.vals.split(",")]:
        tx(U.ATTN + b" %.2f" % args.top); time.sleep(8)
        rf.wr(REG, v)
        print(f"---- 0x{REG:03X} = 0x{rf.rd(REG):02X} (0x110 0x{rf.rd(0x110):02X}, 0x118 0x{rf.rd(0x118):02X}, 0x114 0x{rf.rd(0x114):02X}, 0x117 0x{rf.rd(0x117):02X}, 0x11B 0x{rf.rd(0x11B):02X})", flush=True)
        for att in atts:
            tx(U.ATTN + b" %.2f" % att); time.sleep(args.settle)
            c0, i0, d0, t0 = m.read(SYNC), m.read(IMG), m.read(DROP), time.time()
            g = []
            while time.time() - t0 < args.count:
                g.append(m.read(AGC) & 0x7F); time.sleep(0.1)
            dt = time.time() - t0
            print(f"att {att:4.0f}: sync {((m.read(SYNC) - c0) & 0xFFFFFFFF) / dt:5.1f}/s  img {((m.read(IMG) - i0) & 0xFFFFFFFF) / dt:5.1f}/s  "
                  f"drop {(m.read(DROP) - d0) & 0xFFFFFFFF:3d}  gain min/med/max {min(g)}/{int(np.median(g))}/{max(g)}", flush=True)
finally:
    rf.wr(REG, orig_val)
    f = orig.split() if orig else []
    if len(f) >= 5:
        tx(U.MODE + b" " + f[3].encode()); tx(U.SRC + b" " + f[4].encode()); tx(U.ATTN + b" " + f[2].encode())
    print(f"restored 0x{REG:03X} = 0x{rf.rd(REG):02X}; TX: {tx(U.HELLO)}", flush=True)
