"""Which LVDS invert group is the RX data port: loopback off (antenna input), set 0x03D bit 5 and then 0x03E bit 3;
an inverted RX D5 pair flips bits 11 and 5 of the received samples, moving the noise from ~0 to about +-2048.
Needs jscc-rx and jscc-gui stopped.  bash -lc 'source /etc/profile.d/pynq_venv.sh && python3 lvds_rx_group.py'"""
import time
import numpy as np
from jscc_rx import JsccRx

rx = JsccRx(download=False)
r3d, r3e = rx.rf.rd(0x03D), rx.rf.rd(0x03E)
try:
    for reg, val in ((None, 0), (0x03D, 1 << 5), (0x03E, 1 << 3)):
        rx.rf.wr(0x03D, r3d); rx.rf.wr(0x03E, r3e)
        if reg:
            rx.rf.wr(reg, val)
        time.sleep(0.02)
        x = rx.capture_adc(8192, trig="now", timeout=2.0)
        print(f"{'none' if reg is None else f'0x{reg:03X}=0x{val:02X}'}: mean |I| {np.abs(x.real).mean():7.1f}, "
              f"mean |Q| {np.abs(x.imag).mean():7.1f}")
finally:
    rx.rf.wr(0x03D, r3d); rx.rf.wr(0x03E, r3e)
    print(f"restored 0x3D/0x3E = 0x{rx.rf.rd(0x03D):02X} 0x{rx.rf.rd(0x03E):02X}")
