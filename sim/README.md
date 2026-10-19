# 仿真与验证

| 目录 | 验证对象 | 方法 | 结果 |
|---|---|---|---|
| `network/` | DeepJSCC-Q 编码器（256×256×3 → 32768 个复符号）与浅层解码器（反向）RTL | xsim（XPM，断言打开），输入 DIV2K 验证图，AXI-Stream 输入 valid、输出 ready 随机拉低（占空比 90 % / 80 %），与整数参考模型（`src/model/int_ref.py`，及其 NumPy 独立实现 `golden_np.py`）导出的各层结果逐比特比对 | 编码器、解码器各连续 2 帧比特精确（`results/encoder_xsim_20260930.log`：17 717 420 周期；`results/decoder_xsim_20260930.log`：14 803 719 周期） |
| `phy/` | OFDM TX 基带（长帧、帧缓存不欠载）、OFDM RX（同步、双 LTF 信道估计、SFO / CPE 跟踪）、两板实采数据回放 | TX 测试平台生成并导出帧，RX 测试平台读入激励并导出星座、相位斜率和输出 | 激励与输出文件体积大，未入库，测试平台需在含 FFT / IFFT 等 IP 的 Vivado 工程中运行，不在克隆后即可运行的范围内（见下） |
| `sscc/` | SSCC 基线：发射（扰码、K=7 卷积码、交织、64QAM）→ AWGN → 接收（Viterbi IP） | xsim，`sscc_ref.py` 为 Python 参考 | 无噪声时 0 字节错误，32768 个符号与参考完全一致；各 SNR 与软 / 硬判决下的字节错误数 |

## `network/`：神经网络

| 文件 | 作用 |
|---|---|
| `tb_top.sv` + `run_top_sim.ps1` | 由生成器产生的块级 / 整网顶层，与参考数据逐元素比对，多帧背靠背；`-Measure` 时记录各汇合 FIFO 的峰值占用，用于确定 FIFO 深度（`fifo_sizes.py` 合并结果） |
| `run_top_questa.ps1` | 同上，用 QuestaSim 运行（ROM 改为推断存储器，不需要 XPM 仿真库） |
| `run_decoder_background.ps1`、`run_full_chain_background.ps1` | 整个编码器 / 解码器的长时间后台仿真，产生 `results/` 中的日志 |
| `tb_conv_layer.sv` + `run_conv_sim.ps1` | 单个卷积层（行缓存 + 卷积引擎）与参考数据比对 |
| `tb_gdn.sv` + `run_gdn_sim.ps1` | 单个 GDN / IGDN 层与参考数据比对 |
| `tb_axis_line_buffer.sv` + `run_sim.ps1` | 行缓存：随机帧、两侧随机反压，与本地计算的窗口序列比对（含零填充与帧标志） |
| `tb_mul_serial.sv` | 串行乘法器：设计中用到的各种位宽，随机与边界值 |
| `tb_axis_async_fifo*.sv` | 网络与物理层之间的异步 FIFO：多种配置与最小深度压力测试、复位行为 |
| `tb_phy_interface.sv` | 网络与物理层之间的两个转换模块：`qam_tx`（编码器输出的 12 位潜变量判决为 64QAM 符号）、`rx_frame`（接收符号还原为解码器输入的潜变量） |
| `glbl_stub.sv` | 单独仿真 XPM 原语时需要的全局信号模块 |
| `make_golden.py` | 用 NumPy 整数参考模型生成各层参考数据（见下） |

脚本直接使用仓库目录：网络 RTL 取自 `src/hw/{tx,rx}/rtl/network/rtl/`，块级顶层由 `src/network/gen_rtl_top.py` 生成到
`src/network/gen/`，模型参数取自部署模型 `data/model_shallow/`（编码器 + 浅层解码器），参考数据在 `data/model_shallow/golden/<image>/`；
单层的卷积与 GDN 用例（`run_conv_sim.ps1`、`run_gdn_sim.ps1`）取自第一段训练的 `data/model/`（编码器 + 对称解码器）。Vivado 默认在
`D:\AMDDesignTools\2025.2\Vivado`，其他位置设置环境变量 `XILINX_VIVADO`。

参考数据不入库（原始导出的三幅图共 362 MB），先生成：

```
python sim/network/make_golden.py                       # -> data/model_shallow/golden/div2k_val_00/，118 个文件，约 10 s
python sim/network/make_golden.py --export data/model   # 单层用例的参考数据 -> data/model/golden/div2k_val_00/，184 个文件
```

它用 `golden_np.py` 的整数运算逐层计算一幅 256×256 图像（默认取 `src/sw/rx/assets/about/hardware.jpg` 中心裁剪，
`--input` 可换图），信道为 10 dB AWGN（NumPy 随机数）。原始导出用的是 DIV2K 验证图和 PyTorch 的随机数，因此数值不同；
在同一输入与同一接收符号下，两者全部张量逐比特相同（`golden_np.py --export <导出目录>` 逐张量核对：部署模型115个算子，第一段模型181个张量）。仿真的周期数只取决于调度、与数据无关，因此与 `results/` 中的日志一致。

```
powershell -ExecutionPolicy Bypass -File sim\network\run_sim.ps1                          # 行缓存 7 个用例
powershell -ExecutionPolicy Bypass -File sim\network\run_conv_sim.ps1 -Engines enc.2.skip  # 单个卷积层
powershell -ExecutionPolicy Bypass -File sim\network\run_gdn_sim.ps1 -Layers dec.9.igdn    # 单个 GDN / IGDN 层
powershell -ExecutionPolicy Bypass -File sim\network\run_top_sim.ps1 -First enc.1          # 块级顶层（约 2 min）
powershell -ExecutionPolicy Bypass -File sim\network\run_top_sim.ps1 -First enc.0 -Last latent_idx -Frames 2   # 整个编码器
powershell -ExecutionPolicy Bypass -File sim\network\run_top_sim.ps1 -First rx_in -Last output -Frames 2       # 整个解码器
```

整个编码器约需 2 小时，整个浅层解码器约 16 分钟（2 帧共 13,767,645 个周期）；`run_full_chain_background.ps1 -RunDir <目录>` 在后台依次运行两者并写状态文件。
各脚本的 `-Image` 参数选择参考数据目录，默认 `div2k_val_00`。

## `phy/`：OFDM 物理层

| 文件 | 作用 |
|---|---|
| `tb_tx_long.sv`、`tb_tx_jscc.sv` | OFDM 发射基带（后者为 DeepJSCC-Q 帧格式，PRBS 64QAM 源），按 20 MSPS 读出，检查帧开始后不欠载，导出采样供 RX 仿真 |
| `tb_rx_long.sv`、`tb_rx_jscc.sv` | OFDM 接收基带：两帧分别加正、负 SFO，再加 CFO、多径、AWGN，检查同步、信道估计与 SFO / CPE 跟踪 |
| `tb_cap_2board.sv` | 把两板空口实测的 ADC 采样回放进接收基带 |

这几个测试平台在开发时的 Vivado 工程中运行（OFDM 块用到 FFT / IFFT、CORDIC、DDS 等 IP），激励来自 `tb_tx_*` 的导出或
`board/tests/adc_frames.py` 的实采数据，仓库中不带一键脚本。要运行它们，先用 `build/build_hw.tcl` 建出 RX / TX 工程，
把测试平台加入 `sim_1`，再按测试平台开头的说明准备激励文件。

## `sscc/`：SSCC 基线

```
vivado -mode batch -source run_sim.tcl -tclargs "FRAMES:1 SNR:13"
```

参数：`FRAMES` 帧数，`SNR` 信噪比（dB，省略为无噪声），`HARD:1` 改为硬判决 Viterbi 输入，`DUMP:1` 导出发射符号供 `sscc_ref.py` 比对。
第一次运行在 `sim/sscc/prj/` 建工程并生成 Viterbi IP；每次运行都会重新编译（`HARD` 通过编译宏切换）。
Viterbi IP 的 xci 沿用原工程的输出路径，运行后 `sim/` 下会出现 `OFDM_RX_JSCC.gen/`（已在 `.gitignore` 中），属预期。

| 命令参数 | 结果（每帧 12 272 字节） | 记录 |
|---|---|---|
| `"FRAMES:1 SNR:16"` | 0 字节错误 | `results.txt` |
| `"FRAMES:1 SNR:13"` | 7 | `results.txt` |
| `"FRAMES:1 SNR:11"` | 559 | `results.txt` |
| `"FRAMES:1 DUMP:1"`，再运行 `python sscc_ref.py`（默认读取仿真目录 `prj/sscc_sim.sim/sim_1/behav/xsim/` 中导出的文件） | 0 字节错误；32 768 个符号与 Python 参考逐个相同 | `results2.txt` |
| `"FRAMES:1 SNR:13 HARD:1"` | 2100 | `results2.txt` |

上板后的端到端验证（空口 PSNR–SNR、帧率、分段时延）见 `board/tests/` 与 `data/measurements/`。
