"""With the AD9361 data-port loopback on (0x3F5 bit 0, TX port driven with 0 by the RX bitstream), set each bit of
the LVDS invert registers 0x03D / 0x03E alone and print the word read back on the RX port: which bit inverts which
pair (a TX pair shows up in the loopback the same way as an RX pair). Restores both registers and 0x3F5.
Needs jscc-rx and jscc-gui stopped.
    bash -lc 'source /etc/profile.d/pynq_venv.sh && python3 lvds_invert_scan.py'
"""
import time
import numpy as np
from jscc_rx import JsccRx


def word(rx):
    x = rx.capture_adc(8192, trig="now", timeout=2.0)
    i = x.real.astype(np.int64) & 0xFFF; q = x.imag.astype(np.int64) & 0xFFF
    vi, ci = np.unique(i, return_counts=True); vq, cq = np.unique(q, return_counts=True)
    return f"I 0x{vi[ci.argmax()]:03X} ({ci.max() / len(i):.0%})  Q 0x{vq[cq.argmax()]:03X} ({cq.max() / len(q):.0%})"


rx = JsccRx(download=False)
r3f5, r3d, r3e = rx.rf.rd(0x3F5), rx.rf.rd(0x03D), rx.rf.rd(0x03E)
try:
    rx.rf.wr(0x3F5, r3f5 | 1); time.sleep(0.05)
    print("0x03D=0x00 0x03E=0x00:", word(rx))
    for reg in (0x03D, 0x03E):
        for b in range(8):
            rx.rf.wr(0x03D, 0); rx.rf.wr(0x03E, 0); rx.rf.wr(reg, 1 << b); time.sleep(0.02)
            print(f"0x{reg:03X} bit {b}:", word(rx))
    rx.rf.wr(0x03D, 0xFF); rx.rf.wr(0x03E, 0x0F); time.sleep(0.02)
    print("0x03D=0xFF 0x03E=0x0F (ADI default):", word(rx))
finally:
    rx.rf.wr(0x03D, r3d); rx.rf.wr(0x03E, r3e); rx.rf.wr(0x3F5, r3f5)
    print(f"restored 0x3F5/0x3D/0x3E = 0x{rx.rf.rd(0x3F5):02X} 0x{rx.rf.rd(0x03D):02X} 0x{rx.rf.rd(0x03E):02X}")
