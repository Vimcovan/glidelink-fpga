"""PYNQ driver for the OFDM_JSCC_PS_RX overlay (PYNQ-ZU, PYNQ 3.1.1).

    from jscc_rx import JsccRx
    rx = JsccRx()                      # loads jscc_rx.bit / jscc_rx.hwh next to this file
    rx.status()                        # dict of counters
    img = rx.get_image()               # (256, 256, 3) uint8, next decoded frame
    iq  = rx.capture_adc(65536, trig="frame")
    tm  = rx.telemetry(start_sym=0)    # equalizer stage snapshots

Address map (HPM0_LPD): img_dma 0x8000_0000, cap_dma 0x8001_0000, registers 0x8002_0000, telemetry 0x8004_0000.
"""
import os
import time
import numpy as np
from pynq import Overlay, MMIO, allocate

HERE = os.path.dirname(os.path.abspath(__file__))
REG_BASE, TEL_BASE = 0x8002_0000, 0x8004_0000
IMG_BYTES = 256 * 256 * 3

# register byte offsets (rx_ps_regs.sv)
R = dict(ID=0x00, VERSION=0x04, CTRL=0x08, IMG_CTRL=0x0C, IMG_STATUS=0x10, CAP_LEN=0x14, CAP_CTRL=0x18,
         CAP_STATUS=0x1C, SYNC_CNT=0x20, PHY_FRAMES=0x24, FB_IN=0x28, FB_DROP=0x2C, DEC_SYMS=0x30,
         IMG_FRAMES=0x34, IMG_SENT=0x38, AGC=0x3C, STATUS=0x40, CAP_OVF=0x44)
COUNTERS = ["SYNC_CNT", "PHY_FRAMES", "FB_IN", "FB_DROP", "DEC_SYMS", "IMG_FRAMES", "IMG_SENT", "CAP_OVF"]

# telemetry word map (telemetry.sv): global words, header, sample regions (single buffer)
TM_PRE, TM_CE, TM_CPE, TM_SFO = 0x1000, 0x2000, 0x3000, 0x4000
N_PRE, N_POST = 1408, 1280                     # PRE = LTF1 + LTF2 + 20 symbols (layout 3; 1344 before)


def iq12(words):
    """24-bit {Q[11:0], I[11:0]} words -> complex (Q10 integers)."""
    w = np.asarray(words, dtype=np.uint32)
    i = (w & 0xFFF).astype(np.int32); q = ((w >> 12) & 0xFFF).astype(np.int32)
    i[i > 2047] -= 4096; q[q > 2047] -= 4096
    return i + 1j * q


class JsccRx:
    def __init__(self, bitfile=os.path.join(HERE, "jscc_rx.bit"), download=True):
        self.ol = Overlay(bitfile, download=download)
        self.reg = MMIO(REG_BASE, 0x1_0000)
        self.tel = MMIO(TEL_BASE, 0x4_0000)
        self.img_dma = self.ol.img_dma
        self.cap_dma = self.ol.cap_dma
        self._img_buf = None
        idv = self.rd("ID")
        if idv != 0x4A535258:
            raise RuntimeError(f"unexpected register block ID {idv:08x}")
        self.rf = None
        if self.reg.read(0x04) >= 2:                         # VERSION 2: the AD9361 is set up by the PS
            from ad9361_ps import AD9361
            self.rf = AD9361(self.reg)
            if download or not (self.reg.read(0x48) & 0b100):   # fresh bitstream (or never initialised): init now
                self.rf.init()

    # ---------------------------------------------------------------- registers
    def rd(self, name):
        return self.reg.read(R[name])

    def wr(self, name, value):
        self.reg.write(R[name], int(value) & 0xFFFF_FFFF)

    def status(self):
        s = {k: self.rd(k) for k in COUNTERS}
        agc = self.rd("AGC")
        s.update(AGC_LOCK=agc >> 7 & 1, AGC_GAIN_IDX=agc & 0x7F, MMCM250_LOCKED=self.rd("STATUS") & 1,
                 IMG_PENDING=self.rd("IMG_STATUS") & 1, CAP_BUSY=self.rd("CAP_STATUS") & 1,
                 RX_SOFT_RESET=self.rd("CTRL") & 1)
        return s

    def clear_counters(self):
        self.wr("CTRL", (self.rd("CTRL") & 1) | 2)

    def rx_reset(self, hold_s=0.01):
        self.wr("CTRL", 1); time.sleep(hold_s); self.wr("CTRL", 0)

    # ---------------------------------------------------------------- DMA helper
    @staticmethod
    def _dma_reset(dma, timeout=0.5):
        """AXI DMA soft reset (S2MM DMACR bit 2), then restart the channel. A halted/aborted transfer cannot be
        cleared with pynq's stop(), which waits for a halt that never comes while S2MM waits for data. A reset that
        is still in progress when the channel is started clears it again (the caller checks `running`)."""
        dma.mmio.write(0x30, 0x4)
        t0 = time.time()
        while dma.mmio.read(0x30) & 0x4 and time.time() - t0 < timeout:
            pass
        dma.recvchannel.start()

    @staticmethod
    def _wait(ch, timeout):
        t0 = time.time()
        while not ch.idle:
            if time.time() - t0 > timeout:
                return False
            time.sleep(0.0005)
        return True

    # ---------------------------------------------------------------- decoded image
    def get_image(self, timeout=2.0):
        """Forward the next complete decoded frame to the PS: (256, 256, 3) uint8 (NHWC = H, W, RGB).
        The DMA buffer is kept for the lifetime of the object (a DMA left armed after a timeout must never write
        into freed memory); after a timeout the channel is restarted."""
        if self._img_buf is None:
            self._img_buf = allocate(shape=(IMG_BYTES // 4,), dtype=np.uint32)
        ch = self.img_dma.recvchannel
        ch.transfer(self._img_buf)                           # arm the DMA first, then request a frame
        self.wr("IMG_CTRL", 1)
        if not self._wait(ch, timeout):
            self._dma_reset(self.img_dma)                    # drop the pending transfer (stop() would spin forever)
            raise TimeoutError(f"no decoded frame within {timeout} s (status {self.status()})")
        self._img_buf.invalidate()
        return np.array(self._img_buf).view(np.uint8).reshape(256, 256, 3).copy()

    # ---------------------------------------------------------------- raw ADC capture
    def capture_adc(self, n=65536, trig="frame", timeout=2.0):
        """n raw ADC samples (complex, 12-bit integers); trig = 'now' or 'frame' (2048 samples before the next
        PHY frame start)."""
        buf = allocate(shape=(n,), dtype=np.uint32)
        try:
            if not self.cap_dma.recvchannel.running:         # left halted by an earlier reset: every transfer() would
                self._dma_reset(self.cap_dma)                # fail with "DMA channel not started" from then on
            self.cap_dma.recvchannel.transfer(buf)
            self.wr("CAP_LEN", n)
            self.wr("CAP_CTRL", 1 | ((1 if trig == "frame" else 0) << 1))
            if not self._wait(self.cap_dma.recvchannel, timeout):
                # e.g. trig 'frame' with no TX signal: drop the pending transfer before its buffer is freed (it would
                # be written by the next capture, and a later process's capture would wait for ever)
                self._dma_reset(self.cap_dma)
                raise TimeoutError(f"ADC capture did not complete within {timeout} s")
            buf.invalidate()
            return iq12(np.array(buf))
        finally:
            buf.freebuffer()

    # ---------------------------------------------------------------- telemetry
    def _tw(self, word, value):
        self.tel.write(4 * word, int(value))

    def _tr(self, word, n):
        return np.array(self.tel.array[word:word + n], dtype=np.uint32)

    def telemetry(self, start_sym=None, stages=("pre", "ce", "sfo", "cpe"), period_ms=None):
        """One committed snapshot: dict with header fields and complex arrays (64 per symbol).
        pre = LTF1 (+ LTF2 from layout version 3 on) + 20 data symbols from start_sym, ce/sfo/cpe = 20 data
        symbols; 'sfo' is after the SFO rotation, 'cpe' the final equalizer output."""
        if start_sym is not None:
            self._tw(7, start_sym)
        if period_ms is not None:
            self._tw(3, period_ms)
        self._tw(5, 0)                                       # pause capture while reading (single buffer)
        try:
            g = self._tr(0, 8)
            if g[0] != 0x7E1E0001:
                raise RuntimeError(f"telemetry magic {g[0]:08x}")
            h = self._tr(64, 16)
            out = dict(seq=int(g[1]), timeouts=int(g[4]), version=int(g[6]),
                       start_sym=int(h[2] >> 16), agc=int(h[3]),
                       phy_syms=int(h[4]) | int(h[5]) << 32, phy_frames=int(h[8]) | int(h[9]) << 32,
                       n=dict(pre=int(h[12]), ce=int(h[13]), cpe=int(h[14]), sfo=int(h[15])))
            base = dict(pre=(TM_PRE, N_PRE), ce=(TM_CE, N_POST), cpe=(TM_CPE, N_POST), sfo=(TM_SFO, N_POST))
            for st in stages:
                b, n = base[st]
                if out["n"][st] and out["n"][st] <= 2048:          # captured length (pre: 1344 / 1408 with LTF2)
                    n = out["n"][st]
                out[st] = iq12(self._tr(b, n) & 0xFFFFFF).reshape(-1, 64)
            return out
        finally:
            self._tw(5, 1)


if __name__ == "__main__":
    rx = JsccRx()
    time.sleep(1.0)
    for k, v in rx.status().items():
        print(f"{k:15s} {v}")
