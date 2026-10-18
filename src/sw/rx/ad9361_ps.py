"""AD9361 control from the PS (RX bitstream VERSION >= 2: rx_ps_regs RF_CTRL / SPI_CMD / SPI_STAT).

    from ad9361_ps import AD9361
    rf = AD9361(mmio)                 # MMIO of the rx_ps_regs block
    rf.init("ad9361_rx_init.json")    # reset + full init (the former PL LUT, see lut_to_json.py)
    rf.rd(0x0FB); rf.wr(0x112, 0x4A)  # single registers
    rf.agc_dump()                     # fast-attack AGC configuration + live state

Command line on the board (rx_server must not be in the middle of an SPI access; reads are harmless):
    bash -lc 'source /etc/profile.d/pynq_venv.sh && python3 ad9361_ps.py agc'          # dump
    ... python3 ad9361_ps.py rd 0x112 | wr 0x112 0x4A
"""
import json
import os
import time

RF_CTRL, SPI_CMD, SPI_STAT = 0x48, 0x4C, 0x50
HERE = os.path.dirname(os.path.abspath(__file__))

# fast-attack AGC registers (UG-570 "Gain control", names as in the ADI no-OS / Linux ad9361 driver)
AGC_REGS = [
    (0x0FA, "AGC config 1 (gain ctrl mode)"), (0x0FB, "AGC config 2"), (0x0FC, "AGC config 3 / ovr range size"),
    (0x0FD, "max full/LMT table index"), (0x0FE, "peak overload wait time"), (0x100, "dig gain step / max"),
    (0x101, "AGC lock level"), (0x103, "large LMT / step 3 size"), (0x104, "ADC small overload thresh"),
    (0x105, "ADC large overload thresh"), (0x106, "overload step sizes"), (0x107, "small LMT ovl thresh"),
    (0x108, "large LMT ovl thresh"), (0x109, "state 5 power meas MSB"), (0x10A, "state 5 power meas LSB"),
    (0x110, "fast AGC config 1"), (0x111, "settling delay / AGC config"), (0x112, "post-lock step / energy lost thr"),
    (0x113, "post-lock step / strong sig thr"), (0x114, "low power thr / ADC ovl"), (0x115, "stronger signal unlock"),
    (0x116, "final overrange / opt gain offset"), (0x117, "gain inc step / energy detect cnt"),
    (0x118, "lock level gain incr upper limit"), (0x119, "gain lock exit count"), (0x11A, "initial LMT gain limit"),
    (0x11B, "increment time"), (0x15C, "power measurement duration"), (0x022, "fast attack gain lock delay"),
    (0x014, "ENSM config 1"), (0x015, "ENSM config 2"), (0x017, "ENSM state (RO)"),
    (0x0B0, "Rx1 gain index (RO, full table)"), (0x0B1, "Rx1 LPF gain (RO)"), (0x0B2, "Rx1 dig gain (RO)"),
    (0x2B3, "fast attack state (RO, Rx1 <2:0>, 5 = gain locked)"), (0x1A7, "Rx1 RSSI symbol"),
    (0x1A9, "Rx1 RSSI preamble"),
]


class AD9361:
    def __init__(self, mmio, log=print):
        self.m, self.log = mmio, log

    # ---------------------------------------------------------------- SPI
    def _xfer(self, cmd, timeout=0.05):
        c0 = self.m.read(SPI_STAT) >> 8 & 0xFF
        self.m.write(SPI_CMD, cmd)
        t0 = time.time()
        while (self.m.read(SPI_STAT) >> 8 & 0xFF) == c0:
            if time.time() - t0 > timeout:
                raise TimeoutError(f"AD9361 SPI command 0x{cmd:08x} did not finish")
        return self.m.read(SPI_STAT) & 0xFF

    def wr(self, addr, val):
        self._xfer((addr & 0x3FF) | (val & 0xFF) << 10 | 1 << 24)

    def rd(self, addr):
        return self._xfer((addr & 0x3FF) | 1 << 25)

    # ---------------------------------------------------------------- init
    def ps_mode(self):
        return self.m.read(RF_CTRL) & 1

    def init(self, script=os.path.join(HERE, "ad9361_rx_init.json"), poll_timeout=2.0):
        """hardware reset, run the init script, then release the PL datapath (RF_CTRL init_done). The script is for
        915 MHz; with rf_lo_mhz.txt next to this file it is patched for that carrier first (ad9361_lo.py)."""
        ops = json.load(open(script, encoding="utf-8"))["ops"]
        import ad9361_lo
        lo_mhz = ad9361_lo.configured_mhz(HERE)
        if lo_mhz != ad9361_lo.DEFAULT_MHZ:
            ops = ad9361_lo.patch_init_ops(ops, lo_mhz * 1e6)
            self.log(f"AD9361 PS init for {lo_mhz:g} MHz (rf_lo_mhz.txt)")
        t_start = time.time()
        self.m.write(RF_CTRL, 0b001)                     # PS mode, RESETB low, not done
        time.sleep(0.002)
        self.m.write(RF_CTRL, 0b011)                     # RESETB high
        time.sleep(0.002)
        for k, op in enumerate(ops):
            kind = op[0]
            if kind == "w":
                self.wr(op[1], op[2])
            elif kind == "r":
                self.rd(op[1])
            elif kind == "delay":
                time.sleep(op[1] / 1000)
            elif kind == "poll":
                _, a, mask, want = op[:4]
                t0 = time.time()
                while (self.rd(a) & mask) != want:
                    if time.time() - t0 > poll_timeout:
                        raise TimeoutError(f"init op {k}: 0x{a:03x} & 0x{mask:02x} != 0x{want:02x} ({op[4] if len(op) > 4 else ''})")
                    time.sleep(0.0002)
        self.m.write(RF_CTRL, 0b111)                     # done: RX FIFOs released, ENABLE/TXNRX high
        self.log(f"AD9361 PS init: {len(ops)} ops in {time.time() - t_start:.2f} s, ENSM state {self.rd(0x017) & 0xF}")

    # ---------------------------------------------------------------- AGC
    def agc_dump(self):
        rows = [(a, n, self.rd(a)) for a, n in AGC_REGS]
        for a, n, v in rows:
            self.log(f"0x{a:03X}  0x{v:02X}  {v:3d}  {n}")
        return {a: v for a, _, v in rows}


class AgcGuard:
    """Fast-attack AGC stuck-gain watchdog: PHY sync rate + AD9361 gain index.

    At 20 MSPS the fast-attack AGC sometimes stays locked at a too-low gain when the signal gets weaker (gain lock is
    only left occasionally; EngineerZone thread 78829: a user saw no unlock above 9.4 MSPS, never confirmed by ADI).
    Slightly too low a gain is harmless (12-bit ADC headroom); far too low, the frames drown in ADC noise and the PHY
    stops synchronising. So: if SYNC_CNT (rx_ps_regs 0x20, read only) grew by less than MIN_RATE frames/s over the
    last STALL seconds while the gain index is below the table maximum, the AGC state machine is reset (0x0FA 0x05 ->
    0xE5, as openwifi agc_settings.sh), at most once per HOLDOFF seconds. No RSSI / noise-floor thresholds.
    TX off: the gain settles at ~70 (not gmax), so a reset follows every HOLDOFF s; harmless, the link is back at once.
    SYNC_CNT and the gain (rx_ps_regs 0x3C, from the PL) are plain MMIO reads taken without the shared lock: the
    spectrum thread holds it up to 0.5 s per frame-triggered capture while sync is lost (that delayed recovery to
    ~2-3 s). Only the SPI reset takes the lock.
    """
    SYNC_CNT, AGC = 0x20, 0x3C
    STALL, MIN_RATE, HOLDOFF = 0.5, 5.0, 3.0     # s, frames/s (TX sends 30), s

    def __init__(self, rf, lock=None, log=print):
        self.rf, self.log = rf, log
        self.lock = lock if lock is not None else __import__("threading").Lock()
        self.t_reset, self.resets, self.last = 0.0, 0, None
        self.hist = __import__("collections").deque()             # (time, SYNC_CNT)
        with self.lock:
            self.gmax = rf.rd(0x0FD) & 0x7F                       # max full table index

    def check(self):
        """one check; returns a dict, with reset=True if the AGC was reset."""
        n = self.rf.m.read(self.SYNC_CNT)
        g = self.rf.m.read(self.AGC) & 0x7F                      # current gain index (PL copy, = 0x109)
        now = time.time()
        self.hist.append((now, n))
        while len(self.hist) > 1 and now - self.hist[1][0] >= self.STALL:
            self.hist.popleft()                                  # keep the newest sample at least STALL s old
        t0, n0 = self.hist[0]
        span = now - t0
        rate = (n - n0) % 2 ** 32 / span if span > 0 else None
        stalled = span >= self.STALL and rate < self.MIN_RATE
        reset = stalled and g < self.gmax and now - self.t_reset > self.HOLDOFF
        if reset:
            with self.lock:
                self.rf.wr(0x0FA, 0x05); self.rf.wr(0x0FA, 0xE5)
            self.t_reset, self.resets = now, self.resets + 1
            self.log(f"AGC guard: sync {rate:.1f}/s over {span:.1f} s, gain {g} < {self.gmax} -> AGC reset #{self.resets}")
        self.last = dict(gain=g, sync_rate=None if rate is None else round(rate, 1), span=round(span, 2),
                         stalled=stalled, reset=reset)
        return self.last


if __name__ == "__main__":
    import sys
    from pynq import MMIO
    rf = AD9361(MMIO(0x8002_0000, 0x1_0000))
    if rf.m.read(0x04) < 2:
        sys.exit("bitstream without PS SPI access (rx_ps_regs VERSION < 2)")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "agc"
    if cmd == "agc":
        rf.agc_dump()
    elif cmd == "rd":
        a = int(sys.argv[2], 0); print(f"0x{a:03X} = 0x{rf.rd(a):02X}")
    elif cmd == "wr":
        a, v = int(sys.argv[2], 0), int(sys.argv[3], 0); rf.wr(a, v); print(f"0x{a:03X} <- 0x{v:02X}, reads 0x{rf.rd(a):02X}")
    elif cmd == "init":
        rf.init()
