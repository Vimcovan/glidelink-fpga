"""AD9361 carrier (RF LO) change for the DeepJSCC-Q boards: the ADI tables and the register sequences used by
the ADI Linux / no-OS ad9361 driver (ad9361_rfpll_vco_init, ad9361_calc_rfpll_int_freq, ad9361_load_gt,
ad9361_tx_quad_phase_search).

Default carrier is 915 MHz (the init LUT / ad9361_rx_init.json). Another carrier is selected per board by the file
rf_lo_mhz.txt next to the service scripts (one number, MHz); absent = 915.
  RX: the PS init script is patched before the init (patch_init_ops): RX/TX synthesizer VCO settings, frequency words,
      LO divider and the RX full gain table of the band.
  TX: the PL init (LUT, 915 MHz) and its TX quadrature phase search run at bitstream load; then the PS retunes both
      synthesizers over the SPI command channel (retune) and repeats the TX quadrature calibration phase search
      (tx_quad_search: the calibration converges only in a window of RX NCO phases that moves with the LO).

    python ad9361_lo.py 2440            # print the register writes for 2440 MHz
Tables: analogdevicesinc/linux drivers/iio/adc/ad9361.c (SynthLUT_FDD for an 80 MHz RF PLL reference, full_gain_table).
"""
import os
import time

REF_HZ = 80_000_000             # RF PLL reference: 40 MHz REFCLK x 2 (init: 0x2AB/0x2AC)
RFPLL_MODULUS = 8388593
MIN_VCO_HZ = 6_000_000_000
DEFAULT_MHZ = 915.0
TX_OFS = 0x40                   # TX synthesizer registers = RX + 0x40

# ADI ad9361 driver SynthLUT_FDD, 80 MHz RF PLL reference: (VCO_MHz, output level, varactor, bias ref, bias tcf,
#   cal offset, varactor reference, charge pump current, LF C2, C1, R1, C3, R3), descending VCO_MHz
SYNTH_LUT_80 = [
    (12605, 13, 1, 4, 2, 15, 12, 7, 15, 6, 13, 5, 14),
    (12245, 13, 1, 4, 2, 15, 12, 7, 15, 6, 13, 5, 14),
    (11906, 13, 1, 4, 2, 15, 12, 7, 15, 6, 13, 5, 14),
    (11588, 13, 1, 4, 2, 15, 12, 7, 14, 6, 14, 4, 14),
    (11288, 13, 1, 4, 2, 15, 12, 8, 15, 6, 13, 5, 14),
    (11007, 13, 1, 4, 2, 15, 12, 8, 14, 6, 13, 5, 14),
    (10742, 13, 1, 4, 2, 15, 12, 9, 15, 6, 13, 5, 14),
    (10492, 13, 1, 6, 2, 15, 12, 9, 14, 6, 13, 5, 14),
    (10258, 13, 1, 6, 2, 15, 12, 10, 15, 6, 13, 5, 14),
    (10036, 13, 1, 6, 2, 15, 12, 10, 15, 6, 13, 5, 14),
    (9827, 13, 1, 6, 2, 14, 12, 11, 15, 6, 13, 5, 14),
    (9631, 13, 1, 6, 2, 13, 12, 11, 15, 6, 13, 5, 14),
    (9445, 13, 1, 6, 2, 12, 12, 12, 15, 6, 13, 5, 14),
    (9269, 13, 1, 6, 2, 12, 12, 12, 15, 6, 13, 5, 14),
    (9103, 13, 1, 6, 2, 12, 12, 13, 15, 6, 13, 5, 14),
    (8946, 13, 1, 6, 2, 12, 12, 13, 15, 6, 13, 5, 14),
    (8797, 12, 1, 7, 2, 12, 12, 13, 15, 6, 13, 5, 14),
    (8655, 12, 1, 7, 2, 12, 12, 14, 15, 6, 13, 5, 14),
    (8520, 12, 1, 7, 2, 12, 12, 14, 15, 6, 13, 5, 14),
    (8392, 12, 1, 7, 2, 12, 12, 15, 15, 7, 13, 5, 14),
    (8269, 12, 1, 7, 2, 12, 12, 15, 15, 6, 13, 5, 14),
    (8153, 12, 1, 7, 2, 12, 12, 15, 15, 6, 13, 5, 14),
    (8041, 12, 1, 7, 2, 13, 12, 16, 15, 6, 13, 5, 14),
    (7934, 11, 1, 7, 2, 12, 12, 15, 15, 6, 13, 5, 14),
    (7831, 11, 1, 7, 2, 12, 12, 16, 15, 6, 13, 5, 14),
    (7733, 10, 1, 7, 3, 13, 12, 15, 15, 6, 13, 5, 14),
    (7638, 10, 1, 7, 2, 12, 12, 16, 15, 6, 13, 5, 14),
    (7547, 10, 1, 7, 2, 12, 12, 16, 15, 6, 13, 5, 14),
    (7459, 10, 1, 7, 2, 12, 12, 17, 15, 6, 13, 5, 14),
    (7374, 10, 2, 7, 3, 14, 13, 13, 15, 6, 13, 5, 14),
    (7291, 10, 2, 7, 3, 14, 13, 14, 15, 6, 13, 5, 14),
    (7212, 10, 2, 7, 3, 14, 13, 14, 15, 6, 13, 5, 14),
    (7135, 10, 2, 7, 3, 14, 13, 14, 15, 6, 13, 5, 14),
    (7061, 10, 2, 7, 3, 14, 13, 15, 15, 6, 13, 5, 14),
    (6988, 10, 1, 7, 3, 12, 14, 19, 15, 6, 13, 5, 14),
    (6918, 9, 2, 7, 3, 14, 13, 14, 15, 6, 13, 5, 14),
    (6850, 9, 2, 7, 3, 14, 13, 15, 15, 6, 13, 5, 14),
    (6784, 9, 2, 7, 2, 13, 13, 15, 15, 6, 13, 5, 14),
    (6720, 9, 2, 7, 2, 13, 13, 15, 15, 6, 13, 5, 14),
    (6658, 8, 2, 7, 3, 14, 13, 15, 15, 6, 13, 5, 14),
    (6597, 8, 2, 7, 2, 13, 13, 15, 15, 6, 13, 5, 14),
    (6539, 8, 2, 7, 2, 13, 13, 15, 15, 6, 13, 5, 14),
    (6482, 8, 2, 7, 2, 13, 13, 15, 15, 6, 13, 5, 14),
    (6427, 7, 2, 7, 3, 14, 13, 15, 15, 6, 13, 5, 14),
    (6373, 7, 2, 7, 3, 15, 13, 15, 15, 6, 13, 5, 14),
    (6321, 7, 2, 7, 3, 15, 13, 15, 15, 6, 13, 5, 14),
    (6270, 7, 2, 7, 3, 15, 13, 15, 15, 6, 13, 5, 14),
    (6222, 7, 2, 7, 3, 15, 13, 16, 15, 6, 13, 5, 14),
    (6174, 6, 2, 7, 3, 15, 13, 15, 15, 6, 13, 5, 14),
    (6128, 6, 2, 7, 3, 15, 13, 15, 15, 6, 13, 5, 14),
    (6083, 6, 2, 7, 3, 15, 13, 15, 15, 6, 13, 5, 14),
    (6040, 6, 2, 7, 3, 15, 13, 16, 15, 6, 13, 5, 14),
    (5997, 6, 2, 7, 3, 15, 13, 16, 15, 6, 13, 5, 14),
]
# full gain table 200_1300 MHz: (0x131, 0x132, 0x133) per index
GAIN_200_1300 = [(0, 0, 32), (0, 0, 0), (0, 0, 0), (0, 1, 0), (0, 2, 0), (0, 3, 0), (0, 4, 0), (0, 5, 0), (1, 3, 32), (1, 4, 0), (1, 5, 0), (1, 6, 0), (1, 7, 0), (1, 8, 0), (1, 9, 0), (1, 10, 0), (1, 11, 0), (1, 12, 0), (1, 13, 0), (1, 14, 0), (2, 9, 32), (2, 10, 0), (2, 11, 0), (2, 12, 0), (2, 13, 0), (2, 14, 0), (2, 15, 0), (2, 16, 0), (2, 43, 32), (2, 44, 0), (4, 40, 32), (4, 41, 0), (4, 42, 0), (4, 43, 0), (36, 32, 32), (36, 33, 0), (68, 32, 32), (68, 33, 0), (68, 34, 0), (68, 35, 0), (68, 36, 0), (68, 37, 0), (68, 38, 0), (68, 39, 0), (68, 40, 0), (68, 41, 0), (68, 42, 0), (68, 43, 0), (68, 44, 0), (68, 45, 0), (68, 46, 0), (68, 47, 0), (68, 48, 0), (68, 49, 0), (68, 50, 0), (100, 46, 32), (100, 47, 0), (100, 48, 0), (100, 49, 0), (100, 50, 0), (100, 51, 0), (100, 52, 0), (100, 53, 0), (100, 54, 0), (100, 55, 0), (100, 56, 0), (101, 56, 32), (102, 56, 32), (103, 56, 32), (104, 56, 32), (105, 56, 32), (106, 56, 32), (107, 56, 32), (108, 56, 32), (109, 56, 32), (110, 56, 32), (111, 56, 32)]
# full gain table 1300_4000 MHz: (0x131, 0x132, 0x133) per index
GAIN_1300_4000 = [(0, 0, 32), (0, 0, 0), (0, 0, 0), (0, 1, 0), (0, 2, 0), (0, 3, 0), (0, 4, 0), (0, 5, 0), (1, 3, 32), (1, 4, 0), (1, 5, 0), (1, 6, 0), (1, 7, 0), (1, 8, 0), (1, 9, 0), (1, 10, 0), (1, 11, 0), (1, 12, 0), (1, 13, 0), (1, 14, 0), (2, 9, 32), (2, 10, 0), (2, 11, 0), (2, 12, 0), (2, 13, 0), (2, 14, 0), (2, 15, 0), (2, 16, 0), (2, 43, 32), (2, 44, 0), (4, 39, 32), (4, 40, 0), (4, 41, 0), (4, 42, 0), (4, 43, 0), (36, 33, 32), (36, 34, 0), (68, 32, 32), (68, 33, 0), (68, 34, 0), (68, 35, 0), (68, 36, 0), (68, 37, 0), (68, 38, 0), (68, 39, 0), (68, 40, 0), (68, 41, 0), (68, 42, 0), (68, 43, 0), (68, 44, 0), (68, 45, 0), (68, 46, 0), (68, 47, 0), (68, 48, 0), (68, 49, 0), (100, 46, 32), (100, 47, 0), (100, 48, 0), (100, 49, 0), (100, 50, 0), (100, 51, 0), (100, 52, 0), (100, 53, 0), (100, 54, 0), (100, 55, 0), (100, 56, 0), (101, 56, 32), (102, 56, 32), (103, 56, 32), (104, 56, 32), (105, 56, 32), (106, 56, 32), (107, 56, 32), (108, 56, 32), (109, 56, 32), (110, 56, 32), (111, 56, 32)]
# full gain table 4000_6000 MHz: (0x131, 0x132, 0x133) per index
GAIN_4000_6000 = [(0, 0, 32), (0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 1, 0), (0, 2, 0), (0, 3, 0), (1, 1, 32), (1, 2, 0), (1, 3, 0), (1, 4, 32), (1, 5, 0), (1, 6, 0), (1, 7, 0), (1, 8, 0), (1, 9, 0), (1, 10, 0), (1, 11, 0), (1, 12, 0), (2, 8, 32), (2, 9, 0), (2, 10, 0), (2, 11, 32), (2, 12, 0), (2, 13, 0), (2, 14, 0), (2, 15, 0), (2, 42, 32), (2, 43, 0), (4, 39, 32), (4, 40, 0), (4, 41, 0), (4, 42, 0), (4, 43, 0), (4, 44, 0), (4, 45, 0), (36, 32, 32), (36, 33, 0), (36, 34, 0), (68, 32, 32), (68, 33, 0), (68, 34, 0), (68, 35, 0), (68, 36, 0), (68, 37, 0), (68, 38, 0), (68, 39, 0), (68, 40, 0), (68, 41, 0), (68, 42, 0), (68, 43, 0), (68, 44, 0), (68, 45, 0), (68, 46, 0), (100, 46, 32), (100, 47, 0), (100, 48, 0), (100, 49, 0), (100, 50, 0), (100, 51, 0), (100, 52, 0), (100, 53, 0), (100, 54, 0), (100, 55, 0), (100, 56, 0), (101, 56, 32), (102, 56, 32), (103, 56, 32), (104, 56, 32), (105, 56, 32), (106, 56, 32), (107, 56, 32), (108, 56, 32), (109, 56, 32), (110, 56, 32), (111, 56, 32)]


def configured_mhz(folder):
    """carrier of this board: rf_lo_mhz.txt in `folder`, default 915"""
    p = os.path.join(folder, "rf_lo_mhz.txt")
    try:
        return float(open(p, encoding="ascii").read().split()[0])
    except (OSError, IndexError, ValueError):
        return DEFAULT_MHZ


def synth_words(lo_hz):
    """(integer, fractional, divider field, VCO Hz) as ad9361_calc_rfpll_int_freq; LO = VCO / 2^(div + 1)"""
    f, div = int(lo_hz), -1
    while f <= MIN_VCO_HZ:
        f <<= 1
        div += 1
    integer, rem = divmod(f, REF_HZ)
    frac = (rem * RFPLL_MODULUS + REF_HZ // 2) // REF_HZ
    return integer, frac, div, f


def synth_regs(lo_hz, tx=False):
    """register writes of one synthesizer (RX, or TX with tx=True): VCO settings of the band, then the frequency
    words (writing the integer low byte starts the VCO calibration). The LO divider (0x005) is written separately."""
    integer, frac, div, vco = synth_words(lo_hz)
    r = next(e for e in SYNTH_LUT_80 if e[0] <= vco // 1_000_000)
    _, lvl, var, bref, btcf, cofs, vref, icp, c2, c1, r1, c3, r3 = r
    o = TX_OFS if tx else 0
    return [(0x23A + o, 0x40 | lvl),            # VCO output level, PORB_VCO_LOGIC
            (0x239 + o, 0xC0 | var),            # init ALC value (as the init script) | varactor
            (0x242 + o, bref | btcf << 3),      # VCO bias ref / tcf
            (0x238 + o, cofs << 3),             # VCO cal offset
            (0x245 + o, 0x00),                  # VCO cal ref tcf
            (0x251 + o, vref),                  # varactor reference
            (0x250 + o, 0x70),                  # varactor ref tcf 7, offset 0
            (0x23B + o, 0x80 | icp),            # charge pump current (bit 7 as the init script)
            (0x23E + o, c2 << 4 | c1),          # loop filter C2, C1
            (0x23F + o, r1 << 4 | c3),          # loop filter R1, C3
            (0x240 + o, r3),                    # loop filter R3
            (0x233 + o, frac & 0xFF), (0x234 + o, frac >> 8 & 0xFF), (0x235 + o, frac >> 16 & 0x7F),
            (0x232 + o, integer >> 8 & 0x07), (0x231 + o, integer & 0xFF)]


def lo_div_reg(lo_hz):
    div = synth_words(lo_hz)[2]
    return 0x005, div << 4 | div                # TX [7:4], RX [3:0]: both synthesizers on the same carrier


def gain_table(lo_hz):
    return GAIN_200_1300 if lo_hz <= 1.3e9 else GAIN_1300_4000 if lo_hz <= 4e9 else GAIN_4000_6000


def patch_init_ops(ops, lo_hz):
    """the PS init script (list of ops, ad9361_rx_init.json) for another carrier: synthesizer writes and the RX gain
    table replaced in place; everything else (calibrations, AGC, filters) unchanged and in the same order."""
    rx, tx = dict(synth_regs(lo_hz)), dict(synth_regs(lo_hz, tx=True))
    a5, v5 = lo_div_reg(lo_hz)
    gt = gain_table(lo_hz)
    out, idx, n_syn, n_gt = [], None, 0, 0
    for op in ops:
        op = list(op)
        if op[0] == "w":
            a = op[1]
            if (a in rx or a in tx) and not (a in (0x23B, 0x27B) and op[2] == 0x80):   # 0x80 alone: VCO cal enable
                op[2] = rx.get(a, tx.get(a)); n_syn += 1
            elif a == a5:
                op[2] = v5
            elif a == 0x130:
                idx = op[2]
            elif a in (0x131, 0x132, 0x133) and idx is not None:
                op[2] = gt[idx][a - 0x131] if idx < len(gt) else 0; n_gt += 1
            elif a == 0x137 and op[2] == 0:
                idx = None                          # gain table written
        out.append(op)
    assert n_syn >= 2 * 16 and n_gt >= 3 * len(gt), (n_syn, n_gt)
    return out


def retune(spi, lo_hz, log=print, timeout=1.0):
    """runtime carrier change of both synthesizers (ENSM in ALERT meanwhile); spi(addr[, value]) -> read data"""
    s014 = spi(0x014)
    spi(0x014, 0x0D)                                 # LEVEL_MODE | FORCE_ALERT_STATE | TO_ALERT
    _wait(spi, 0x017, 0x0F, 0x05, timeout, "ENSM ALERT")
    for a, v in synth_regs(lo_hz) + synth_regs(lo_hz, tx=True):
        spi(a, v)
    spi(*lo_div_reg(lo_hz))
    _wait(spi, 0x247, 0x02, 0x02, timeout, "RX RF PLL lock")
    _wait(spi, 0x287, 0x02, 0x02, timeout, "TX RF PLL lock")
    spi(0x014, s014)
    _wait(spi, 0x017, 0x0F, 0x0A, timeout, "ENSM FDD")
    integer, frac, div, vco = synth_words(lo_hz)
    log(f"AD9361 LO {lo_hz / 1e6:.3f} MHz: VCO {vco / 1e6:.1f} MHz, int {integer} frac {frac} div {div}, PLLs locked")


def tx_quad_search(spi, log=print, rx_nco_freq=2, m_decim=3, default_phase=4, timeout=1.0):
    """TX quadrature calibration with the RX NCO phase search (as ad9361_txq_search.v / no-OS
    ad9361_tx_quad_phase_search): calibrate at phases 0..31, take the centre of the longest circular run of
    converged phases, calibrate there. Returns (phase, converged bit field, run length, final 0x0A7 status)."""
    s014 = spi(0x014)
    spi(0x014, 0x0D)
    _wait(spi, 0x017, 0x0F, 0x05, timeout, "ENSM ALERT")
    s169 = spi(0x169)
    spi(0x169, s169 & 0xFC)                          # RX quadrature tracking off

    def cal(phase):
        spi(0x0A0, rx_nco_freq << 5 | phase)
        spi(0x0A1, 0b011111 << 2 | m_decim)          # SETTLE | DC | GAIN | PHASE | SOFT_RESET
        spi(0x0A1, 0b011110 << 2 | m_decim)
        spi(0x016, 0x10)                             # start TX quadrature calibration
        _wait(spi, 0x016, 0x10, 0x00, timeout, "TX quad cal")
        return spi(0x0A7) & 0x03

    field = sum((cal(p) == 3) << p for p in range(32))
    best_len = best_start = 0
    for s in range(32):
        n = 0
        while n < 32 and field >> ((s + n) % 32) & 1:
            n += 1
        if n > best_len:
            best_len, best_start = n, s
    phase = (best_start + best_len // 2) % 32 if best_len else default_phase
    status = cal(phase)
    spi(0x169, s169)
    spi(0x014, s014)
    _wait(spi, 0x017, 0x0F, 0x0A, timeout, "ENSM FDD")
    log(f"TX quad cal: phase {phase} (run {best_len}, field {field:08x}), status {status:02b}")
    return phase, field, best_len, status


def _wait(spi, addr, mask, want, timeout, what):
    t0 = time.time()
    while spi(addr) & mask != want:
        if time.time() - t0 > timeout:
            raise TimeoutError(f"AD9361: {what} (0x{addr:03x} & 0x{mask:02x} != 0x{want:02x})")
        time.sleep(0.0002)


if __name__ == "__main__":
    import sys
    f = float(sys.argv[1]) * 1e6 if len(sys.argv) > 1 else DEFAULT_MHZ * 1e6
    print(synth_words(f))
    for a, v in synth_regs(f) + synth_regs(f, tx=True) + [lo_div_reg(f)]:
        print(f"0x{a:03X} = 0x{v:02X} ({v})")
