"""Generate a structural SystemVerilog top for a range of network blocks from the export.

Every op of the selected blocks becomes one stream node; the tool inserts the glue:
  * conv engines (memory plan, merged engines included) with their window buffer:
    3x3 -> axis_line_buffer, 1x1 -> axis_pixel_buffer, snooping 1x1 skip -> centre_tap
    on a fork of the host engine window stream; merged engines -> axis_split
  * gdn/igdn -> axis_gdn, add -> axis_add, sigmoid -> axis_sigmoid, gate -> axis_gate
  * axis_fork where a tensor has several consumers
  * delay FIFOs (axis_fifo_packed) on the inputs of joins (add, gate); their depth comes
    from fifo_sizes.json (measured in simulation, see sim/run_top_sim.ps1) or, with
    --measure, a large default so that simulation can record the peak occupancy.
Ports of the generated module (name blk_<first>_<last>):
  s_in_tdata[11:0], s_in_tvalid, s_in_tready                     block input tensor (NHWC)
  m_out_tdata[Y-1:0], m_out_tvalid, m_out_tready, m_out_tlast, m_out_tuser[0:0]

  python gen_rtl_top.py enc.3                 -> gen/blk_enc_3.sv
  python gen_rtl_top.py enc.0 enc.8 --measure
Paths default to the repository layout (--export data/model, --fifo-sizes src/network/fifo_sizes.json,
--out src/network/gen); regen_hw.py runs the whole flow and updates src/hw.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXP = ROOT.parents[1] / 'data' / 'model'          # export: manifest.json, params/, rtl_init/
INIT = EXP / 'rtl_init'
SIZES = ROOT / 'fifo_sizes.json'                  # measured join-FIFO depths (sim/network/fifo_sizes.py)
MEASURE_DEPTH = 16384            # elements per join FIFO in --measure mode
MARGIN = lambda peak: peak + max(64, peak // 8)


def ident(s: str) -> str:
    # merged engines are named <block>.conv_plus_skip; instances keep the short form <block>_conv_skip
    return re.sub(r'[^0-9a-zA-Z]', '_', s.replace('_plus_', '+'))


def q(s) -> str:
    return f'"{s}"'


def block_of(name: str) -> str:
    p = name.split('.')
    return '.'.join(p[:2])


def fifo_shape(n: int, w: int):
    """(PACK, DEPTH_W, RAM_STYLE) of the cheapest FIFO holding n elements of w bits."""
    if n <= 128:
        depth = 1 << max(4, math.ceil(math.log2(n + 1)))
        return 1, depth, 'distributed'
    pack = max(1, 36 // w)
    depth = math.ceil((n + pack - 1) / pack)
    depth = 512 * math.ceil(depth / 512)
    return pack, depth, 'block'


class Top:
    def __init__(self, blocks: list[str], measure: bool):
        self.m = json.loads((EXP / 'manifest.json').read_text(encoding='utf-8'))
        self.measure = measure
        if not measure and not SIZES.exists():
            raise FileNotFoundError(f'{SIZES}: join-FIFO depths missing (use --measure to size them in simulation)')
        self.sizes = {} if measure else json.loads(SIZES.read_text(encoding='utf-8'))
        order = [block_of(o['out']) for o in self.m['ops'] if o['op'] not in ('input',)]
        uniq = list(dict.fromkeys(order))
        i0, i1 = uniq.index(blocks[0]), uniq.index(blocks[-1])
        self.blocks = uniq[i0:i1 + 1]
        self.ops = [o for o in self.m['ops'] if o['op'] != 'input' and block_of(o['out']) in self.blocks]
        # the receiver symbols are the source of rx_input (not a tensor of the network)
        self.ops = [dict(o, **{'in': 'rx_symbols'}) if o['op'] == 'rx_input' else o for o in self.ops]
        self.m['tensors'].setdefault('rx_symbols', {'shape_hwc': [1, 32768, 2]})
        self.byname = {o.get('name', o['out']): o for o in self.m['ops']}
        self.tensors = self.m['tensors']
        plan = [e for p in ('encoder', 'decoder') for e in self.m['memory_plan'][p]['engines']]
        self.bufs = {b['name']: b for p in ('encoder', 'decoder') for b in self.m['memory_plan'][p]['buffers']}
        names = {o.get('name', o['out']) for o in self.ops}
        self.engines = [e for e in plan if 'GDN' not in e['note'] and all(n in names for n in e['ops'])]
        self.eng_of = {n: e for e in self.engines for n in e['ops']}
        produced = {o['out'] for o in self.ops}
        ins = [t for o in self.ops for t in (o['in'] if isinstance(o['in'], list) else [o['in']]) if t not in produced]
        self.inputs = list(dict.fromkeys(ins))
        assert len(self.inputs) == 1, f'blocks must have one input tensor, got {self.inputs}'
        consumed = {t for o in self.ops for t in (o['in'] if isinstance(o['in'], list) else [o['in']])}
        outs = [o['out'] for o in self.ops if o['out'] not in consumed]
        assert len(outs) == 1, outs
        self.output = outs[0]
        self.L = []          # declarations
        self.B = []          # body
        self.cons = {}       # tensor -> list of consumer ids
        self.fifos = []      # (key, elements)

    # ------------------------------------------------------------------ helpers
    def shape(self, t):
        s = self.tensors[t]['shape_hwc']
        return s if len(s) == 3 else [1] + list(s)

    def width(self, t):
        if any(o.get('op') == 'rx_input' and o.get('in') == t for o in self.ops):
            return 24
        return 17 if self.byname.get(t, {}).get('op') == 'sigmoid' else 12

    def decl_stream(self, p, w, user=1):
        self.L.append(f'    logic [{w - 1}:0] {p}_d; logic {p}_v, {p}_r, {p}_l; logic [{user - 1}:0] {p}_u;')

    def consume(self, t, cid):
        self.cons.setdefault(t, []).append(cid)
        return f'{ident(t)}__{cid}'

    # ------------------------------------------------------------------ nodes
    def engine(self, e):
        name = e['name']
        j = json.loads((INIT / name / 'engine.json').read_text(encoding='utf-8'))
        d = (INIT / name).as_posix()
        t_in = j['in']
        h, w, c = self.shape(t_in)
        k, s = j['K'], j['STRIDE']
        wn = f'w_{ident(name)}'
        self.decl_stream(wn, 12, 3)
        snoop = 'centre tap of' in e['note']
        if snoop:
            host = e['note'].split('centre tap of ')[1].strip()
            src = self.consume(f'@win:{host}', f'snoop_{ident(name)}')
            hj = json.loads((INIT / host / 'engine.json').read_text(encoding='utf-8'))
            self.B.append(f'''    centre_tap #(.DATA_W(12), .CIN({c}), .K({hj["K"]}), .PAD({hj["PAD"]}), .GH({hj["GROUPS"]}), .GS({j["GROUPS"]})) u_tap_{ident(name)} (
        .clk(clk), .rst_n(rst_n), .s_tdata({src}_d), .s_tvalid({src}_v), .s_tready({src}_r), .s_tlast({src}_l), .s_tuser({src}_u),
        .m_tdata({wn}_d), .m_tvalid({wn}_v), .m_tready({wn}_r), .m_tlast({wn}_l), .m_tuser({wn}_u));''')
        else:
            src = self.consume(t_in, f'win_{ident(name)}')
            if k == 1 and s == 1:
                self.B.append(f'''    axis_pixel_buffer #(.DATA_W(12), .C({c}), .NPIX({h * w}), .GROUPS({j["GROUPS"]})) u_pb_{ident(name)} (
        .clk(clk), .rst_n(rst_n), .s_in_tdata({src}_d), .s_in_tvalid({src}_v), .s_in_tready({src}_r),
        .m_out_tdata({wn}_d), .m_out_tvalid({wn}_v), .m_out_tready({wn}_r), .m_out_tlast({wn}_l), .m_out_tuser({wn}_u));''')
            else:
                pack, style = self.lb_shape(name)
                rows = max(k, s)
                # one extra row lets the writer run a further row ahead (decouples bursty
                # producers such as pixel shuffle); taken only when it costs no extra RAM
                gran = 4096 if style == 'ultra' else 512
                words = lambda r: math.ceil(r * w * c / pack)
                if not self.measure and math.ceil(words(rows + 1) / gran) == math.ceil(words(rows) / gran):
                    rows += 1
                self.B.append(f'''    axis_line_buffer #(.DATA_W(12), .C({c}), .W({w}), .H({h}), .K({k}), .STRIDE({s}), .PAD({j["PAD"]}), .ROWS({rows}),
        .GROUPS({j["GROUPS"]}), .PACK({pack}), .RAM_STYLE({q(style)})) u_lb_{ident(name)} (
        .clk(clk), .rst_n(rst_n), .s_in_tdata({src}_d), .s_in_tvalid({src}_v), .s_in_tready({src}_r),
        .m_out_tdata({wn}_d), .m_out_tvalid({wn}_v), .m_out_tready({wn}_r), .m_out_tlast({wn}_l), .m_out_tuser({wn}_u));''')
        # the window stream itself may be snooped: route it through the consumer mechanism
        win = f'@win:{name}'
        self.win_src[win] = wn
        ein = self.consume(win, f'eng_{ident(name)}')
        on = f'o_{ident(name)}' if len(j['out']) > 1 else ident(j['out'][0])
        self.decl_stream(on, 12)
        self.B.append(f'''    conv_engine #(.CIN({j["CIN"]}), .COUT({j["COUT"]}), .K({k}), .P({j["P"]}), .ACT({q(j["ACT"])}), .ACT2({q(j["ACT2"])}), .ACT_SPLIT({j["ACT_SPLIT"]}),
        .WROM_PREFIX({q(d + "/wrom")}), .WROM_STYLE({q(j["WROM_STYLE"])}), .WROM_BANK_DEPTH({j["WROM_BANK_DEPTH"]}),
        .WROM_MODE({q(j["WROM_MODE"])}), .WG_NCOL({j["WG_NCOL"]}), .RQ_MUL({q(j["RQ_MUL"])}),
        .PRE({j["PRE"]}), .SH_MIN({j["SH_MIN"]}), .SH_BITS({j["SH_BITS"]}),
        .BIAS_FILE({q(d + "/bias.mem")}), .PRE_FILE({q(d + "/pre.mem")}), .M_FILE({q(d + "/m.mem")}), .SH_FILE({q(d + "/sh.mem")})) u_{ident(name)} (
        .clk(clk), .rst_n(rst_n), .s_in_tdata({ein}_d), .s_in_tvalid({ein}_v), .s_in_tready({ein}_r), .s_in_tlast({ein}_l), .s_in_tuser({ein}_u),
        .m_out_tdata({on}_d), .m_out_tvalid({on}_v), .m_out_tready({on}_r), .m_out_tlast({on}_l), .m_out_tuser({on}_u));''')
        if len(j['out']) > 1:
            t0, t1 = j['out']
            ho, wo, _ = self.shape(t0)
            a, b = ident(t0), ident(t1)
            self.decl_stream(a, 12); self.decl_stream(b, 12)
            self.B.append(f'''    axis_split #(.DATA_W(12), .C({j["COUT"]}), .SPLIT({j["ACT_SPLIT"]}), .NPIX({ho * wo})) u_split_{ident(name)} (
        .clk(clk), .rst_n(rst_n), .s_tdata({on}_d), .s_tvalid({on}_v), .s_tready({on}_r),
        .m0_tdata({a}_d), .m0_tvalid({a}_v), .m0_tready({a}_r), .m0_tlast({a}_l), .m0_tuser({a}_u),
        .m1_tdata({b}_d), .m1_tvalid({b}_v), .m1_tready({b}_r), .m1_tlast({b}_l), .m1_tuser({b}_u));''')

    def lb_shape(self, engine):
        base = engine.rsplit('.', 1)[0]
        for key in (f'{engine} line buffer', f'{base} line buffer', f'{base}.conv/skip line buffer'):
            b = self.bufs.get(key)
            if b:
                if b['bits'] != 12:
                    return 3, 'block'            # 12-bit data path (input image widened)
                return (b['upack'], 'ultra') if b['loc'] == 'URAM' else (b['pack'], 'block')
        return 3, 'block'

    def join_input(self, op, port, t):
        """consumer port of a join (add/gate) with its delay FIFO; returns stream prefix."""
        src = self.consume(t, f'{ident(op["out"])}_{port}')
        key = f'{op["out"]}.{port}'
        w = self.width(t) + (2 if port == 'a' else 0)            # a carries {tuser, tlast}
        n = MEASURE_DEPTH if self.measure else self.sizes.get(key, MEASURE_DEPTH)
        # sizes are MARGIN(peak) = peak + 64 (min): <= 72 means the measured peak was <= 8
        # elements, which the producer's own 16-deep output FIFO absorbs -> no join FIFO
        if n <= MARGIN(8):
            return src
        # Keep the generated FIFO mapping consistent with memory_plan.py.  The
        # planner may move a measured delay FIFO to URAM to stay under the
        # BRAM36 target; previously join_input always selected block RAM here.
        plan_buf = None
        if not self.measure and '.out.' in key:
            prefix, suffix = key.rsplit('.out.', 1)
            if suffix == 'x':
                plan_name = f'{prefix} identity delay'
            elif suffix == 'b':
                plan_name = f'{prefix}.skip delay' if prefix == block_of(prefix) else f'{prefix} identity delay'
            else:
                plan_name = ''
            plan_buf = self.bufs.get(plan_name) if plan_name else None
        if plan_buf is not None and plan_buf.get('loc') == 'URAM':
            pack = max(1, 72 // w)
            depth = math.ceil((n + pack - 1) / pack)
            style = 'ultra'
        else:
            pack, depth, style = fifo_shape(n, w)
        self.fifos.append((key, n, w, pack, depth, style))
        fn = f'q_{ident(key)}'
        self.decl_stream(fn, 12 if port != 'g' else 17)
        payload = f'{{{src}_u, {src}_l, {src}_d}}' if port == 'a' else f'{src}_d'
        outp = f'{{{fn}_u, {fn}_l, {fn}_d}}' if port == 'a' else f'{fn}_d'
        self.B.append(f'''    axis_fifo_packed #(.DATA_W({w}), .PACK({pack}), .DEPTH_W({depth}), .RAM_STYLE({q(style)})) u_fifo_{ident(key)} (
        .clk(clk), .rst_n(rst_n), .s_tdata({payload}), .s_tvalid({src}_v), .s_tready({src}_r),
        .m_tdata({outp}), .m_tvalid({fn}_v), .m_tready({fn}_r));''')
        if port != 'a':
            self.B.append(f'    assign {fn}_l = 1\'b0; assign {fn}_u = 1\'b0;')
        return fn

    def gdn(self, op):
        g = json.loads((INIT / op['out'] / 'gdn.json').read_text(encoding='utf-8'))
        d = (INIT / op['out']).as_posix()
        src = self.consume(op['in'], f'gdn_{ident(op["out"])}')
        o = ident(op['out'])
        self.decl_stream(o, 12)
        self.B.append(f'''    axis_gdn #(.C({g["C"]}), .LANES({g["LANES"]}), .INVERSE({g["INVERSE"]}), .X2_SHIFT({g["X2_SHIFT"]}), .LSH({g["LSH"]}), .F({g["F"]}),
        .QW({g["QW"]}), .DW({g["DW"]}), .NUNITS({g["NUNITS"]}), .PRE({g["PRE"]}), .M({g["M"]}), .SH({g["SH"]}),
        .GAMMA_FILE({q(d + "/gamma.mem")}), .BETA_FILE({q(d + "/beta.mem")})) u_{o} (
        .clk(clk), .rst_n(rst_n), .s_tdata({src}_d), .s_tvalid({src}_v), .s_tready({src}_r), .s_tlast({src}_l), .s_tuser({src}_u),
        .m_tdata({o}_d), .m_tvalid({o}_v), .m_tready({o}_r), .m_tlast({o}_l), .m_tuser({o}_u));''')

    def add(self, op):
        a = self.join_input(op, 'a', op['in'][0])
        b = self.join_input(op, 'b', op['in'][1])
        o = ident(op['out'])
        self.decl_stream(o, 12)
        self.B.append(f'''    axis_add #(.A_W(12), .Y_W(12), .MA({op["Ma"]}), .MB({op["Mb"]}), .SH({op["sh"]}), .RELU({int(bool(op["relu"]))}), .U_W(1)) u_{o} (
        .clk(clk), .rst_n(rst_n), .a_tdata({a}_d), .a_tvalid({a}_v), .a_tready({a}_r), .a_tlast({a}_l), .a_tuser({a}_u),
        .b_tdata({b}_d), .b_tvalid({b}_v), .b_tready({b}_r),
        .m_tdata({o}_d), .m_tvalid({o}_v), .m_tready({o}_r), .m_tlast({o}_l), .m_tuser({o}_u));''')

    def gate(self, op):
        x = self.join_input(op, 'x', op['in'][0])
        a = self.join_input(op, 'a', op['in'][1])
        g = self.join_input(op, 'g', op['in'][2])
        o = ident(op['out'])
        self.decl_stream(o, 12)
        self.B.append(f'''    axis_gate #(.X_W(12), .Y_W(12), .MA({op["Ma"]}), .MB({op["Mb"]}), .SH({op["sh"]}), .U_W(1)) u_{o} (
        .clk(clk), .rst_n(rst_n), .x_tdata({x}_d), .x_tvalid({x}_v), .x_tready({x}_r),
        .a_tdata({a}_d), .a_tvalid({a}_v), .a_tready({a}_r), .a_tlast({a}_l), .a_tuser({a}_u),
        .g_tdata({g}_d), .g_tvalid({g}_v), .g_tready({g}_r),
        .m_tdata({o}_d), .m_tvalid({o}_v), .m_tready({o}_r), .m_tlast({o}_l), .m_tuser({o}_u));''')

    def sigmoid(self, op):
        tab = self.m['sigmoid_tables'][op['table']]
        src = self.consume(op['in'], f'sig_{ident(op["out"])}')
        o = ident(op['out'])
        out8 = op['op'] == 'sigmoid_out'
        self.decl_stream(o, 8 if out8 else 17)
        self.B.append(f'''    axis_sigmoid #(.X_W(12), .M312({op["to_q312_M"]}), .SH312({op["to_q312_sh"]}), .NSEG({tab["nseg"]}), .OB({tab["offset_bits"]}),
        .SHIFT({tab["shift"]}), .C0_FILE({q((EXP / tab["c0"]["file"]).as_posix())}), .C1_FILE({q((EXP / tab["c1"]["file"]).as_posix())}),
        .OUT8({int(out8)}), .U_W(1)) u_{o} (
        .clk(clk), .rst_n(rst_n), .s_tdata({src}_d), .s_tvalid({src}_v), .s_tready({src}_r), .s_tlast({src}_l), .s_tuser({src}_u),
        .m_tdata({o}_d), .m_tvalid({o}_v), .m_tready({o}_r), .m_tlast({o}_l), .m_tuser({o}_u));''')

    def pwl(self, op):
        """per-channel piecewise-linear activation (shallow decoder export, export_shallow.py)"""
        f = op['files']
        src = self.consume(op['in'], f'pwl_{ident(op["out"])}')
        o = ident(op['out'])
        self.decl_stream(o, 12)
        pre = int((EXP / f['rq_pre']['file']).read_text(encoding='ascii').split()[0], 16)
        p = lambda k: q((EXP / f[k]['file']).as_posix())
        self.B.append(f'''    axis_pwl #(.X_W(12), .C({op["channels"]}), .NK({op["NK"]}), .T0({op["T0"]}), .SL({op["STEP_LOG2"]}), .YW({f["yk"]["bits"]}), .PRE({pre}),
        .SLOPE_FILE({p("slope")}), .YK_FILE({p("yk")}), .M_FILE({p("rq_M")}), .SH_FILE({p("rq_sh")}), .U_W(1)) u_{o} (
        .clk(clk), .rst_n(rst_n), .s_tdata({src}_d), .s_tvalid({src}_v), .s_tready({src}_r), .s_tlast({src}_l), .s_tuser({src}_u),
        .m_tdata({o}_d), .m_tvalid({o}_v), .m_tready({o}_r), .m_tlast({o}_l), .m_tuser({o}_u));''')

    def pixel_shuffle(self, op):
        r = op['factor']
        src = self.consume(op['in'], f'ps_{ident(op["out"])}')
        o = ident(op['out'])
        self.decl_stream(o, 12)
        if r == 1:
            self.B.append(f'    assign {o}_d = {src}_d; assign {o}_v = {src}_v; assign {src}_r = {o}_r; '
                          f'assign {o}_l = {src}_l; assign {o}_u = {src}_u;')
            return
        h, w, c = self.shape(op['in'])
        blk = block_of(op['out'])
        kind = "skip" if ".skip" in op["in"] else "main"
        b = self.bufs.get(f'{blk}.shuffle row ({kind})')
        style = 'distributed' if b is None or b['elems'] * 12 <= 4096 else ('ultra' if b['loc'] == 'URAM' else 'block')
        # optional drain FIFO (memory plan '<blk>.shuffle drain (main)'): lets the shuffle read its row buffer out at
        # full rate when the consumer is slow, so that the input side (the conv engine) is not stalled meanwhile
        drain = self.bufs.get(f'{blk}.shuffle drain ({kind})')
        p = f'{o}_ps' if drain else o
        if drain:
            self.decl_stream(p, 12)
        self.B.append(f'''    axis_pixel_shuffle #(.DATA_W(12), .C({c}), .R({r}), .W({w}), .H({h}), .RB_STYLE({q(style)})) u_{o} (
        .clk(clk), .rst_n(rst_n), .s_tdata({src}_d), .s_tvalid({src}_v), .s_tready({src}_r),
        .m_tdata({p}_d), .m_tvalid({p}_v), .m_tready({p}_r), .m_tlast({p}_l), .m_tuser({p}_u));''')
        if drain:
            if drain['loc'] == 'URAM':
                pack = 72 // 14; depth = math.ceil(drain['elems'] / pack); dstyle = 'ultra'
            else:
                pack, depth, dstyle = fifo_shape(drain['elems'], 14)
            self.fifos.append((f'{op["out"]} drain', drain['elems'], 14, pack, depth, dstyle))
            self.B.append(f'''    axis_fifo_packed #(.DATA_W(14), .PACK({pack}), .DEPTH_W({depth}), .RAM_STYLE({q(dstyle)})) u_drain_{o} (
        .clk(clk), .rst_n(rst_n), .s_tdata({{{p}_u, {p}_l, {p}_d}}), .s_tvalid({p}_v), .s_tready({p}_r),
        .m_tdata({{{o}_u, {o}_l, {o}_d}}), .m_tvalid({o}_v), .m_tready({o}_r));''')

    def out_width(self, t):
        kind = self.byname.get(t, {}).get('op')
        return {'sigmoid_out': 8, 'qam': 24}.get(kind, 12)

    def qam(self, op):
        h, w, c = self.shape(op['in'])
        thr = op['thresholds']
        src = self.consume(op['in'], f'qam_{ident(op["out"])}')
        o = ident(op['out'])
        self.decl_stream(o, 24)
        tp = ', '.join(f'.T{i}({v})' for i, v in enumerate(thr))
        self.B.append(f'''    qam_tx #(.X_W(12), .C({c}), {tp}) u_{o} (
        .clk(clk), .rst_n(rst_n), .s_tdata({src}_d), .s_tvalid({src}_v), .s_tready({src}_r),
        .s_tlast({src}_l), .s_tuser({src}_u),
        .m_tdata({o}_d), .m_tvalid({o}_v), .m_tready({o}_r), .m_tlast({o}_l), .m_tuser({o}_u));''')

    def rx_input(self, op):
        h, w, c = self.shape(op['out'])
        src = self.consume(op['in'], f'rx_{ident(op["out"])}')
        o = ident(op['out'])
        self.decl_stream(o, 12)
        self.B.append(f'''    rx_frame #(.X_W(12), .H({h}), .W({w}), .C({c})) u_{o} (
        .clk(clk), .rst_n(rst_n), .s_tdata({src}_d), .s_tvalid({src}_v), .s_tready({src}_r),
        .s_tlast({src}_l), .s_tuser({src}_u),
        .m_tdata({o}_d), .m_tvalid({o}_v), .m_tready({o}_r), .m_tlast({o}_l), .m_tuser({o}_u));''')

    # ------------------------------------------------------------------ build
    def build(self):
        self.win_src = {}
        done = set()
        for op in self.ops:
            nm = op.get('name', op['out'])
            if op['op'] == 'conv':
                e = self.eng_of[nm]
                if e['name'] not in done:
                    done.add(e['name'])
                    self.engine(e)
            elif op['op'] in ('gdn', 'igdn'):
                self.gdn(op)
            elif op['op'] == 'add':
                self.add(op)
            elif op['op'] == 'gate':
                self.gate(op)
            elif op['op'] in ('sigmoid', 'sigmoid_out'):
                self.sigmoid(op)
            elif op['op'] == 'qam':
                self.qam(op)
            elif op['op'] == 'rx_input':
                self.rx_input(op)
            elif op['op'] == 'pixel_shuffle':
                self.pixel_shuffle(op)
            elif op['op'] == 'pwl':
                self.pwl(op)
            else:
                raise NotImplementedError(op['op'])
        # forks: every stream with several consumers
        forks = []
        for t, cids in self.cons.items():
            src = self.win_src[t] if t.startswith('@win:') else ident(t)
            tw = self.width(t)
            wd = 24 if tw == 24 else (17 if (not t.startswith('@win:') and tw == 17) else 12)
            uw = 3 if t.startswith('@win:') else 1
            if len(cids) == 1:
                c = f'{ident(t)}__{cids[0]}'
                self.L.append(f'    logic [{wd - 1}:0] {c}_d; logic {c}_v, {c}_r, {c}_l; logic [{uw - 1}:0] {c}_u;')
                forks.append(f'    assign {c}_d = {src}_d; assign {c}_v = {src}_v; assign {c}_l = {src}_l; '
                             f'assign {c}_u = {src}_u; assign {src}_r = {c}_r;')
                continue
            n, pw = len(cids), wd + 1 + uw
            f = f'fk_{ident(t)}'
            self.L.append(f'    logic [{pw - 1}:0] {f}_d; logic [{n - 1}:0] {f}_v, {f}_r;')
            body = [f'''    axis_fork #(.W({pw}), .N({n})) u_{f} (.clk(clk), .rst_n(rst_n),
        .s_tdata({{{src}_u, {src}_l, {src}_d}}), .s_tvalid({src}_v), .s_tready({src}_r),
        .m_tdata({f}_d), .m_tvalid({f}_v), .m_tready({f}_r));''']
            for i, cid in enumerate(cids):
                c = f'{ident(t)}__{cid}'
                self.L.append(f'    logic [{wd - 1}:0] {c}_d; logic {c}_v, {c}_r, {c}_l; logic [{uw - 1}:0] {c}_u;')
                body.append(f'    assign {{{c}_u, {c}_l, {c}_d}} = {f}_d; assign {c}_v = {f}_v[{i}]; assign {f}_r[{i}] = {c}_r;')
            forks += body
        return forks

    def emit(self) -> tuple[str, str]:
        forks = self.build()
        ti = self.inputs[0]
        hi, wi, ci = self.shape(ti)
        to = self.output
        yw = self.out_width(to)
        mod = f'blk_{ident(self.blocks[0])}' + (f'_{ident(self.blocks[-1])}' if len(self.blocks) > 1 else '')
        i, o = ident(ti), ident(to)
        in_op = self.byname.get(ti, {}).get('op')
        in_is_symbol = any(x.get('op') == 'rx_input' and x.get('in') == ti for x in self.ops)
        in_w = 24 if in_is_symbol else 12
        in_elems = (hi * wi * ci // 2) if in_is_symbol else hi * wi * ci
        out_op = self.byname.get(to, {}).get('op')
        out_is_symbol = out_op == 'qam'
        out_elems = (self.shape(to)[1] if out_is_symbol else self.shape(to)[0] * self.shape(to)[1] * self.shape(to)[2])
        head = [
            chr(96) + 'timescale 1ns / 1ps',
            '// GENERATED by gen_rtl_top.py from data/model - do not edit.',
            f'// blocks {", ".join(self.blocks)}: {ti} {self.shape(ti)} -> {to} {self.shape(to)}',
            f'// join FIFOs ({"measure mode" if self.measure else "sized from fifo_sizes.json"}):',
        ] + [f'//   {k:28s} {n:6d} elems x {w:2d} b -> PACK {p} x DEPTH {d} {s}' for k, n, w, p, d, s in self.fifos] + [
            f'module {mod} (',
            '    input  logic        clk,',
            '    input  logic        rst_n,',
            f'    input  logic [{in_w - 1}:0] s_in_tdata,',
            '    input  logic        s_in_tvalid,',
            '    output logic        s_in_tready,',
            '    input  logic        s_in_tlast,',
            '    input  logic [0:0]  s_in_tuser,',
            f'    output logic [{yw - 1}:0] m_out_tdata,',
            '    output logic        m_out_tvalid,',
            '    input  logic        m_out_tready,',
            '    output logic        m_out_tlast,',
            '    output logic [0:0]  m_out_tuser',
            ');',
        ]
        self.L.insert(0, f'    logic [{in_w - 1}:0] {i}_d; logic {i}_v, {i}_r, {i}_l; logic [0:0] {i}_u;')
        if in_is_symbol:
            io = [f'    assign {i}_d = s_in_tdata; assign {i}_v = s_in_tvalid; assign s_in_tready = {i}_r;',
                  f'    assign {i}_l = s_in_tlast; assign {i}_u = s_in_tuser;',
                  f'    assign m_out_tdata = {o}_d; assign m_out_tvalid = {o}_v; assign {o}_r = m_out_tready;',
                  f'    assign m_out_tlast = {o}_l; assign m_out_tuser = {o}_u;']
        else:
            io = [f'    assign {i}_d = s_in_tdata; assign {i}_v = s_in_tvalid; assign s_in_tready = {i}_r;',
                  f'    axis_tag #(.C({ci}), .NPIX({hi * wi})) u_tag_in (.clk(clk), .rst_n(rst_n), .valid({i}_v), .ready({i}_r), .tlast({i}_l), .tuser({i}_u));',
                  f'    assign m_out_tdata = {o}_d; assign m_out_tvalid = {o}_v; assign {o}_r = m_out_tready;',
                  f'    assign m_out_tlast = {o}_l; assign m_out_tuser = {o}_u;']
        text = '\n'.join(head + self.L + [''] + io + forks + [''] + self.B + ['endmodule', ''])
        return mod, text


def main():
    global EXP, INIT, SIZES
    ap = argparse.ArgumentParser()
    ap.add_argument('first')
    ap.add_argument('last', nargs='?')
    ap.add_argument('--measure', action='store_true')
    ap.add_argument('--export', type=Path, default=EXP)
    ap.add_argument('--fifo-sizes', type=Path, default=SIZES)
    ap.add_argument('--out', type=Path, default=ROOT / 'gen')
    a = ap.parse_args()
    EXP, SIZES = a.export.resolve(), a.fifo_sizes.resolve()
    INIT = EXP / 'rtl_init'
    t = Top([a.first, a.last or a.first], a.measure)
    mod, text = t.emit()
    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{mod}.sv').write_text(text, encoding='ascii', newline='\n')
    hi, wi, ci = t.shape(t.inputs[0])
    ho, wo, co = t.shape(t.output)
    in_op = t.byname.get(t.inputs[0], {}).get('op')
    in_is_symbol = any(x.get('op') == 'rx_input' and x.get('in') == t.inputs[0] for x in t.ops)
    out_op = t.byname.get(t.output, {}).get('op')
    info = {'module': mod, 'in': t.inputs[0], 'out': t.output, 'H': hi, 'W': wi, 'CIN': ci,
            'HO': ho, 'WO': wo, 'COUT': co, 'OUT_W': t.out_width(t.output),
            'IN_W': 24 if in_is_symbol else 12,
            'IN_ELEMS': hi * wi * ci // 2 if in_is_symbol else hi * wi * ci,
            'OUT_ELEMS': wo if out_op == 'qam' else ho * wo * co,
            'fifos': [{'key': k, 'elems': n, 'width': w, 'pack': p, 'depth': d, 'style': s}
                      for k, n, w, p, d, s in t.fifos]}
    (out / f'{mod}.json').write_text(json.dumps(info, indent=1), encoding='utf-8', newline='\n')
    print(f'{mod}: {len(t.engines)} engines, {len(t.fifos)} join FIFOs -> {out / mod}.sv')


if __name__ == '__main__':
    main()
