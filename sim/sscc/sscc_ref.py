"""Python reference of the SSCC chain (sscc_tx.sv / sscc_rx.sv), independent of the RTL:
    TX: bytes -> 802.11 scrambler (x^7 + x^4 + 1, seed 1011101, per frame) + zero tail -> 802.11n LDPC n = 1944 rate 2/3
        (101 codewords) + 264 zero bits -> Gray 64-QAM 158 * {+-1..+-7}
    RX: the LDPC decoder model of src/model/ldpc_ref.py, run on the LLRs the RTL decoder received
Checks the files dumped by tb_sscc (+DUMP=1):
    tx_bytes.txt / tx_syms.txt      first frame: RTL symbols vs reference
    dec_llr.txt / dec_out.txt       decoder input LLRs and, per codeword, ok / iterations / 1296 output bits

    python sscc_ref.py [sim dir]      (default: the xsim run directory of run_sim.tcl, prj/sscc_sim.sim/sim_1/behav/xsim)
"""
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'model'))
import ldpc_ref as L                                          # noqa: E402

PAY_BYTES, FRAME_SYMS, CW = 16360, 32768, 101
FRAME_BITS = CW * L.K


def scramble(bits, seed=0b1011101):
    st = [(seed >> (6 - i)) & 1 for i in range(7)]          # st[0] = bit 6 ... st[6] = bit 0
    out = np.empty_like(bits)
    for n, b in enumerate(bits):
        fb = st[0] ^ st[3]                                    # bit 6 ^ bit 3
        out[n] = b ^ fb
        st = st[1:] + [fb]                                    # {st[5:0], fb}
    return out


def tx(payload, S):
    assert len(payload) == PAY_BYTES
    bits = np.unpackbits(np.frombuffer(payload, np.uint8))  # MSB first
    bits = np.concatenate([scramble(bits), np.zeros(FRAME_BITS - len(bits), np.uint8)])
    cb = np.concatenate([L.encode(S, bits[c * L.K:(c + 1) * L.K]) for c in range(CW)]
                        + [np.zeros(FRAME_SYMS * 6 - CW * L.N, np.uint8)])
    return L.qam_map(cb)                                      # (I, Q)


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else f"{os.path.dirname(os.path.abspath(__file__))}/prj/sscc_sim.sim/sim_1/behav/xsim"
    S = L.base_matrix()
    if os.path.exists(f"{d}/tx_syms.txt"):
        pay = bytes(int(l, 16) for l in open(f"{d}/tx_bytes.txt"))
        rtl = np.loadtxt(f"{d}/tx_syms.txt", dtype=int)
        ref = tx(pay, S)
        n = min(len(rtl), len(ref))
        bad = np.flatnonzero(np.any(rtl[:n] != ref[:n], axis=1))
        print(f"TX: reference {len(ref)} symbols, RTL {len(rtl)}: {len(bad)} mismatches"
              + (f", first at {bad[0]}: rtl {rtl[bad[0]]} ref {ref[bad[0]]}" if len(bad) else ""))
    if os.path.exists(f"{d}/dec_out.txt"):
        llr = np.loadtxt(f"{d}/dec_llr.txt", dtype=int)
        outs = [l.split() for l in open(f"{d}/dec_out.txt")]
        nbad = nfail = 0; its = []
        for c, (ok, it, bits) in enumerate(outs):
            if (c + 1) * L.N > len(llr):
                break
            h, rok, rit = L.decode(S, llr[c * L.N:(c + 1) * L.N])
            rb = ''.join(str(b) for b in h[:L.K])
            same = (rb == bits) and (int(ok) == int(rok)) and (int(it) == rit)
            nbad += not same; nfail += not rok; its.append(rit)
            if not same and nbad <= 3:
                print(f"  codeword {c}: RTL ok {ok} it {it}, ref ok {int(rok)} it {rit}, "
                      f"bit mismatches {sum(a != b for a, b in zip(rb, bits))}")
        print(f"DEC: {len(its)} codewords, {nbad} differ from the reference "
              f"(reference: {nfail} not converged, iterations mean {np.mean(its):.2f} max {max(its)})")
