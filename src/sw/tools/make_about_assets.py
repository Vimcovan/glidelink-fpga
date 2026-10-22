"""Pictures of the RX GUI About page (src/sw/rx/assets/about/), in two palettes: light (assets/about/, for the light
themes) and dark (assets/about/dark/, for the dark ones). Facts as in the design report (tables 3-3, 3-11, sections
2.2.3.3 and 2.2.3.6); the PSNR curves are the measured sweep data/measurements/psnr_snr/k0802_final_da.json with the
selection of report/figures/sim/meas_plots.py.

    python make_about_assets.py
"""
import json
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
OUT = os.path.join(ROOT, "src", "sw", "rx", "assets", "about")
FONT = "Microsoft YaHei"
W, H, DPI = 12.0, 7.6, 100                          # 1200 x 760 px

PAL = {
    "light": dict(BG="#FFFFFF", INK="#0F172A", MUTED="#64748B", LINE="#CBD5E1", ACC="#2563EB", SEC="#EA580C",
                  ACC_SOFT="#DBEAFE", SEC_SOFT="#FFEDD5", PANEL="#F1F5F9", GRID="#E2E8F0"),
    "dark": dict(BG="#0D1628", INK="#E2E8F0", MUTED="#8A9AB5", LINE="#2A3A58", ACC="#22D3EE", SEC="#F59E0B",
                 ACC_SOFT="#0C2A3A", SEC_SOFT="#33270E", PANEL="#0B1222", GRID="#1C2A44"),
}


def canvas(P, title):
    fig = plt.figure(figsize=(W, H), dpi=DPI, facecolor=P["BG"])
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis("off")
    ax.add_patch(plt.Rectangle((0.35, H - 0.95), 0.08, 0.5, color=P["ACC"], lw=0))
    ax.text(0.6, H - 0.7, title, fontsize=26, color=P["INK"], va="center", weight="bold")
    ax.plot([0.35, W - 0.35], [H - 1.2, H - 1.2], color=P["LINE"], lw=1.2)
    return fig, ax


def box(ax, P, x, y, w, h, text, fill=None, edge=None, fs=19, color=None, bold=False, dashed=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.12", fc=fill or P["PANEL"],
                                ec=edge or P["LINE"], lw=2, ls="--" if dashed else "-"))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs, color=color or P["INK"],
            weight="bold" if bold else "normal", linespacing=1.35)


def arrow(ax, P, x0, y0, x1, y1, color=None):
    ax.add_patch(FancyArrowPatch((x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=22, lw=2,
                                 color=color or P["MUTED"], shrinkA=0, shrinkB=0))


def save(fig, P, name):
    d = OUT if P is PAL["light"] else os.path.join(OUT, "dark")
    os.makedirs(d, exist_ok=True)
    fig.savefig(os.path.join(d, name), dpi=DPI, facecolor=fig.get_facecolor())
    plt.close(fig)


# ------------------------------------------------------------------ 1 comparison set-up
def fairness(P):
    fig, ax = canvas(P, "对照条件：同一物理层，同样的符号数")
    y1, y2, h = 4.45, 2.45, 1.15
    ax.text(0.45, y1 + h + 0.25, "SSCC 分离式编码（基线）", fontsize=19, color=P["SEC"], weight="bold")
    xs = [(0.45, 1.1, "图像"), (1.9, 2.0, "JPEG\n≤ 16,348 字节"), (4.25, 1.0, "扰码"),
          (5.6, 2.65, "LDPC 2/3\n802.11n，n = 1944"), (8.6, 1.25, "64QAM")]
    for k, (x, w, t) in enumerate(xs):
        box(ax, P, x, y1, w, h, t, fill=P["SEC_SOFT"] if 0 < k else P["PANEL"], fs=18)
        if k:
            arrow(ax, P, xs[k - 1][0] + xs[k - 1][1] + 0.05, y1 + h / 2, x - 0.05, y1 + h / 2)
    ax.text(0.45, y2 + h + 0.25, "DeepJSCC-Q 联合编码", fontsize=19, color=P["ACC"], weight="bold")
    xs2 = [(0.45, 1.1, "图像"), (1.9, 4.6, "DeepJSCC-Q 编码器（神经网络）"), (6.85, 3.0, "64QAM 星座点")]
    for k, (x, w, t) in enumerate(xs2):
        box(ax, P, x, y2, w, h, t, fill=P["ACC_SOFT"] if 0 < k else P["PANEL"], fs=18)
        if k:
            arrow(ax, P, xs2[k - 1][0] + xs2[k - 1][1] + 0.05, y2 + h / 2, x - 0.05, y2 + h / 2)
    box(ax, P, 10.3, y2, 1.35, y1 + h - y2, "同一\nOFDM\n发射链\n\nAD9361", fill=P["PANEL"], edge=P["MUTED"], fs=18)
    arrow(ax, P, 9.9, y1 + h / 2, 10.25, y1 + h / 2); arrow(ax, P, 9.9, y2 + h / 2, 10.25, y2 + h / 2)
    ax.text(W / 2, 1.45, "每幅图像 32,768 个 64QAM 符号 = 683 个 OFDM 符号 · 发射功率相同", ha="center", fontsize=20,
            color=P["INK"], weight="bold")
    ax.text(W / 2, 0.8, "同一组板卡与同一 OFDM 发射链，运行时切换", ha="center", fontsize=16, color=P["MUTED"])
    save(fig, P, "fairness.png")


# ------------------------------------------------------------------ 2 measured PSNR vs SNR (report figure data)
def psnr_snr(P):
    da = json.load(open(os.path.join(ROOT, "data", "measurements", "psnr_snr", "k0802_final_da.json"), encoding="utf-8"))
    fig = plt.figure(figsize=(W, H), dpi=DPI, facecolor=P["BG"])
    t = fig.add_axes([0, 0, 1, 1]); t.axis("off"); t.set_xlim(0, W); t.set_ylim(0, H)
    t.add_patch(plt.Rectangle((0.35, H - 0.95), 0.08, 0.5, color=P["ACC"], lw=0))
    t.text(0.6, H - 0.7, "板上实测：PSNR 随信道 SNR 的变化", fontsize=26, color=P["INK"], va="center", weight="bold")
    ax = fig.add_axes([0.1, 0.14, 0.86, 0.68], facecolor=P["BG"])
    for mode, name, col, mk in (("jscc", "DeepJSCC-Q", P["ACC"], "o"), ("sscc", "SSCC（JPEG + LDPC + 64QAM）", P["SEC"], ">")):
        pts = sorted((r["snr_da_db"], r["psnr_shown"]) for r in da["results"]
                     if r["mode"] == mode and r.get("snr_da_db") is not None and r.get("psnr_shown") is not None
                     and (r.get("snr_da_excl") or 0) < 0.05 and not (mode == "jscc" and (r.get("rx_fps") or 0) < 25))
        x, y = np.array(pts).T
        ax.plot(x, y, "-" + mk, color=col, lw=2.6, ms=8, mfc=P["BG"], mew=2, label=name)
    ax.set_ylim(7, 33.5); ax.set_yticks(range(8, 34, 4)); ax.set_xlim(1.5, 37.5); ax.set_xticks(range(3, 37, 3))
    ax.set_xlabel("数据辅助 SNR / dB", fontsize=18, color=P["INK"]); ax.set_ylabel("PSNR / dB", fontsize=18, color=P["INK"])
    ax.tick_params(colors=P["MUTED"], labelsize=15)
    for s in ax.spines.values():
        s.set_color(P["LINE"])
    ax.grid(color=P["GRID"], lw=1)
    lg = ax.legend(loc="lower right", fontsize=17, frameon=False)
    for tx in lg.get_texts():
        tx.set_color(P["INK"])
    save(fig, P, "psnr_snr.png")


# ------------------------------------------------------------------ 3 constellation constraint
def constellation(P):
    fig, ax = canvas(P, "星座约束：输出直接是 64QAM 星座点")
    rng = np.random.default_rng(3)
    lv = np.arange(-7, 8, 2)
    for k, (x0, title, sub, col) in enumerate(((0.6, "原始 DeepJSCC", "任意复数值：需要能直接送入 I/Q 的专用接口", P["SEC"]),
                                               (6.4, "DeepJSCC-Q", "64QAM 星座点：直接接入标准 OFDM 发射链", P["ACC"]))):
        a = fig.add_axes([x0 / W + 0.06, 0.2, 0.33, 0.52], facecolor=P["BG"])
        if k == 0:
            z = rng.normal(0, 3.2, (900, 2))
        else:
            z = np.array([(i, q) for i in lv for q in lv], float)
        a.scatter(z[:, 0], z[:, 1], s=14 if k == 0 else 46, color=col, alpha=0.75 if k == 0 else 1, lw=0)
        a.set_xlim(-9.5, 9.5); a.set_ylim(-9.5, 9.5); a.set_aspect("equal")
        a.axhline(0, color=P["LINE"], lw=1); a.axvline(0, color=P["LINE"], lw=1)
        a.set_xticks([]); a.set_yticks([])
        for s in a.spines.values():
            s.set_color(P["LINE"])
        ax.text(x0 + 2.5, H - 1.75, title, ha="center", fontsize=21, color=col, weight="bold")
        ax.text(x0 + 2.5, 0.85, sub, ha="center", fontsize=16, color=P["INK"])
    ax.text(6.0, 3.9, "→", ha="center", va="center", fontsize=48, color=P["MUTED"])
    ax.text(6.0, 3.1, "训练中\n量化约束", ha="center", va="center", fontsize=15, color=P["MUTED"])
    save(fig, P, "globecom_iq.png")


# ------------------------------------------------------------------ 4 one frame = one image
def frame_length(P):
    fig, ax = canvas(P, "一帧 = 一幅图像")
    x0, x1, y, h = 0.6, 11.4, 4.0, 1.1
    tot = 320 + 683 * 80 + 1000                      # preamble, data symbols, 50 us gap at 20 MSPS
    xs = lambda n: x0 + (x1 - x0) * n / tot
    box(ax, P, xs(0), y, xs(320) - xs(0) + 0.35, h, "", fill=P["SEC_SOFT"], edge=P["SEC"])
    box(ax, P, xs(320) + 0.4, y, xs(320 + 683 * 80) - xs(320) - 0.4, h, "683 个 OFDM 数据符号（每个 64 + CP 16 采样）",
        fill=P["ACC_SOFT"], edge=P["ACC"], fs=19)
    box(ax, P, xs(320 + 683 * 80) + 0.05, y, x1 - xs(320 + 683 * 80) - 0.05, h, "", fill=P["PANEL"])
    ax.text(xs(0) + 0.2, y - 0.4, "前导 320", ha="center", fontsize=15, color=P["SEC"])
    ax.text(x1 - 0.25, y - 0.4, "帧间隔", ha="center", fontsize=15, color=P["MUTED"])
    ax.annotate("", (x1, y + h + 0.35), (x0, y + h + 0.35), arrowprops=dict(arrowstyle="<->", color=P["MUTED"], lw=1.8))
    ax.text(W / 2, y + h + 0.6, "54,960 个采样 · 2.75 ms（20 MSPS）", ha="center", fontsize=19, color=P["INK"])
    for k, t in enumerate(("每个符号 48 个数据子载波 + 4 个导频",
                           "32,768 个 64QAM 符号 = 682 × 48 + 32，一幅 256×256 图像",
                           "整帧 2.75 ms 内都要保持同步，采样频偏在帧内的累积不能忽略")):
        ax.text(0.6, 2.55 - k * 0.65, "· " + t, fontsize=18, color=P["INK"] if k < 2 else P["ACC"])
    save(fig, P, "frame_length.png")


# ------------------------------------------------------------------ 5 SFO phase over the frame
def sfo_phase(P):
    fig = plt.figure(figsize=(W, H), dpi=DPI, facecolor=P["BG"])
    t = fig.add_axes([0, 0, 1, 1]); t.axis("off"); t.set_xlim(0, W); t.set_ylim(0, H)
    t.add_patch(plt.Rectangle((0.35, H - 0.95), 0.08, 0.5, color=P["ACC"], lw=0))
    t.text(0.6, H - 0.7, "采样频偏：不跟踪时各子载波的相位旋转", fontsize=26, color=P["INK"], va="center", weight="bold")
    ax = fig.add_axes([0.1, 0.2, 0.62, 0.6], facecolor=P["BG"])
    k = np.arange(-26, 27)
    for frac, name, col, ls in ((0.0, "帧头", P["MUTED"], "-"), (0.5, "帧中", P["SEC"], "-"), (1.0, "帧尾", P["ACC"], "-")):
        drift = 54960 * 1e-6 * frac                   # samples of timing drift at 1 ppm
        ax.plot(k, np.degrees(2 * np.pi * k * drift / 64), ls, color=col, lw=2.6, label=name)
    ax.axhspan(-7.7, 7.7, color=P["ACC_SOFT"], alpha=0.8, lw=0)
    ax.text(-25, 6.2, "64QAM 最外角点判决容限 ±7.7°", fontsize=14, color=P["INK"])
    ax.set_xlim(-27, 27); ax.set_ylim(-10.5, 10.5)
    ax.set_xlabel("子载波", fontsize=17, color=P["INK"]); ax.set_ylabel("相位旋转 / °", fontsize=17, color=P["INK"])
    ax.tick_params(colors=P["MUTED"], labelsize=14)
    for s in ax.spines.values():
        s.set_color(P["LINE"])
    ax.grid(color=P["GRID"], lw=1)
    lg = ax.legend(loc="lower right", fontsize=15, frameon=False)
    for tx in lg.get_texts():
        tx.set_color(P["INK"])
    for j, (a, b) in enumerate((("1 ppm", "帧尾边缘子载波约 8°，\n已超过判决容限"),
                                ("40 ppm", "两板晶振各 ±20 ppm 的最坏\n情况：帧尾约 5.6 rad"),
                                ("对策", "逐符号跟踪：4 个导频估计\nCPE 与相位斜率，二阶环\n对斜坡输入无稳态误差"))):
        y = 5.6 - j * 1.75
        t.text(9.0, y, a, fontsize=19, color=P["ACC"] if j < 2 else P["SEC"], weight="bold")
        t.text(9.0, y - 0.25, b, fontsize=15, color=P["INK"], va="top", linespacing=1.4)
    t.text(0.6, 0.45, "一帧 54,960 个采样；1 ppm → 帧尾定时漂移 0.055 个采样；相位 = 2π·k·漂移/64", fontsize=13,
           color=P["MUTED"])
    save(fig, P, "sfo_phase.png")


# ------------------------------------------------------------------ 6 layer-pipelined convolution engines
def our_arch(P):
    fig, ax = canvas(P, "硬件：逐层流水卷积引擎")
    for k, (name, p) in enumerate((("第 1 层", "P = 8"), ("第 2 层", "P = 16"), ("第 3 层", "P = 4"))):
        x = 0.6 + k * 3.25
        ax.add_patch(FancyBboxPatch((x, 3.9), 2.7, 2.15, boxstyle="round,pad=0,rounding_size=0.12", fc=P["PANEL"],
                                    ec=P["ACC"], lw=2))
        ax.text(x + 1.35, 5.65, name, ha="center", fontsize=19, color=P["INK"], weight="bold")
        box(ax, P, x + 0.25, 4.75, 2.2, 0.55, "行缓存 K−1 行", fill=P["SEC_SOFT"], edge=P["SEC"], fs=15)
        ax.text(x + 1.35, 4.25, f"{p} 路乘累加", ha="center", fontsize=16, color=P["INK"])
        arrow(ax, P, x + 2.75, 4.97, x + 3.2, 4.97)
    ax.text(10.5, 4.97, "…", fontsize=30, color=P["MUTED"], va="center")
    rows = (("行缓存", "每层只缓存 K−1 行输入，不存整幅特征图；层间带反压流水"),
            ("并行度 P", "按各层运算量，逐层取满足 30 fps 的最小值"),
            ("权重 ROM", "按尺寸选 LUT ROM、BRAM 宽字 ROM 或顺序读 ROM"))
    for j, (a, b) in enumerate(rows):
        y = 2.85 - j * 0.85
        ax.text(0.6, y, a, fontsize=19, color=P["ACC"], weight="bold")
        ax.text(2.6, y, b, fontsize=18, color=P["INK"])
    ax.text(W - 0.4, 0.3, "示意图：各层 P 值为示例", ha="right", fontsize=13, color=P["MUTED"])
    save(fig, P, "our_arch.png")


# ------------------------------------------------------------------ 7 comparison with the published FPGA design
def compare_table(P):
    fig, ax = canvas(P, "与已发表的 FPGA 实现对比")
    rows = [("", "Isobe 等（GLOBECOM 2025）", "本作品"),
            ("器件", "ZCU111（XCZU28DR）", "2 × xczu5eg"),
            ("名义卷积工作量 / 帧", "0.222 GMAC", "1.154 GMAC（5.2 倍）"),
            ("DSP（编 / 解）", "1,597 / 874", "233 / 51"),
            ("BRAM36（编 / 解）", "1,038.5 / 1,011.5", "100 / 43"),
            ("物理层", "5G 系统（外部设备）", "自研 OFDM，同在 PL"),
            ("功耗", "编 7.76 W / 解 5.69 W", "整板 3.07 W / 3.44 W")]
    cx, cw = (0.5, 3.75, 7.75), (3.25, 4.0, 3.85)
    top, rh = H - 1.6, 0.68
    for i, r in enumerate(rows):
        y = top - (i + 1) * rh
        if i == 0:
            ax.add_patch(plt.Rectangle((cx[0], y), sum(cw), rh, color=P["ACC"], lw=0))
        elif i % 2 == 0:
            ax.add_patch(plt.Rectangle((cx[0], y), sum(cw), rh, color=P["PANEL"], lw=0))
        for j, t in enumerate(r):
            col = P["BG"] if i == 0 else (P["ACC"] if j == 2 else (P["MUTED"] if j == 0 else P["INK"]))
            ax.text(cx[j] + (0.15 if j == 0 else cw[j] / 2), y + rh / 2, t, ha="left" if j == 0 else "center", va="center",
                    fontsize=18, color=col, weight="bold" if (i == 0 or j == 2) else "normal")
    ax.text(0.5, 0.75, "资源为绝对数量；两者的器件、数值精度与功耗测量方法不同（报告 3.3.6 节）。", fontsize=14, color=P["MUTED"])
    save(fig, P, "compare_table.png")


# ------------------------------------------------------------------ hardware photo with labels
def hardware(P):
    im = Image.open(os.path.join(ROOT, "report", "figures", "results", "hardware_45.jpg")).convert("RGB")
    im = im.resize((1200, round(im.height * 1200 / im.width)), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    f1 = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 24); f2 = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 18)
    acc = P["ACC"]
    labels = (((350, 140), (40, 40), "接收端 · 触摸屏", "实时显示重建图像"),
              ((430, 320), (330, 440), "RX · PYNQ-ZU", "ZU5EG，解码器与 OFDM 接收"),
              ((250, 285), (30, 560), "AD9361 射频子卡", "接收"),
              ((70, 260), (30, 330), "天线", ""),
              ((860, 560), (860, 700), "TX · PYNQ-ZU", "ZU5EG，编码器与 OFDM 发射"),
              ((690, 520), (400, 650), "AD9361 射频子卡", "发射"),
              ((640, 700), (420, 750), "天线", ""),
              ((1020, 410), (930, 250), "USB 摄像头", "发射端图像来源"))
    for (px, py), (lx, ly), a, b in labels:
        tw = max(d.textlength(a, font=f1), d.textlength(b, font=f2) if b else 0) + 24
        th = 40 + (26 if b else 0)
        cx, cy = lx + tw / 2, ly + th / 2
        d.line([(cx, cy), (px, py)], fill=acc, width=3)
        d.ellipse([px - 7, py - 7, px + 7, py + 7], fill=acc, outline="white", width=2)
        d.rounded_rectangle([lx, ly, lx + tw, ly + th], radius=10, fill=P["BG"], outline=acc, width=3)
        d.text((lx + 12, ly + 6), a, font=f1, fill=P["INK"])
        if b:
            d.text((lx + 12, ly + 40), b, font=f2, fill=P["MUTED"])
    d2 = OUT if P is PAL["light"] else os.path.join(OUT, "dark")
    os.makedirs(d2, exist_ok=True)
    im.save(os.path.join(d2, "hardware.jpg"), quality=92)


plt.rcParams["font.family"] = FONT
plt.rcParams["axes.unicode_minus"] = False
for P in PAL.values():
    for fn in (fairness, psnr_snr, constellation, frame_length, sfo_phase, our_arch, compare_table, hardware):
        fn(P)
print("written", OUT)
