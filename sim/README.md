# 仿真与验证

| 目录 | 验证对象 | 方法 | 结果 |
|---|---|---|---|
| `network/` | DeepJSCC-Q 编码器（256×256×3 → 32768 个复符号）与浅层解码器（反向）RTL | xsim（XPM，断言打开），输入 DIV2K 验证图，AXI-Stream 输入 valid、输出 ready 随机拉低（占空比 90 % / 80 %），与整数参考模型（`src/model/int_ref.py`，及其 NumPy 独立实现 `golden_np.py`）导出的各层结果逐比特比对 | 编码器、解码器各连续 2 帧比特精确（`results/encoder_xsim_20260930.log`：17 717 420 周期；`results/decoder_xsim_20260930.log`：14 803 719 周期） |
| `phy/` | OFDM TX 基带（长帧、帧缓存不欠载）、OFDM RX（同步、双 LTF 信道估计、SFO / CPE 跟踪）、两板实采数据回放；同步模块的合成激励与实采回放（`phy/sync/`） | TX 测试平台生成并导出帧，RX 测试平台读入激励并导出星座、相位斜率和输出 | 激励与输出文件体积大，未入库，测试平台需在含 FFT / IFFT 等 IP 的 Vivado 工程中运行，不在克隆后即可运行的范围内（见下） |
| `sscc/` | SSCC 基线：发射（扰码、802.11n LDPC 编码、64QAM）→ AWGN → 接收（软解映射、分层最小和 LDPC 译码） | xsim，`sscc_ref.py` + `src/model/ldpc_ref.py` 为 Python 参考 | 无噪声时 0 字节错误，32768 个符号与参考完全一致；15.5 / 14.5 dB 下各 202 个码字的译码输出与逐位参考模型完全一致 |

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
| `sync/` | 同步模块（帧检测、频偏纠正、LTF 定时）的文件激励仿真：合成激励与板上实采采样回放，一键脚本见下 |

这几个测试平台在开发时的 Vivado 工程中运行（OFDM 块用到 FFT / IFFT、CORDIC、DDS 等 IP），激励来自 `tb_tx_*` 的导出或
`board/tests/adc_frames.py` 的实采数据，仓库中不带一键脚本。要运行它们，先用 `build/build_hw.tcl` 建出 RX / TX 工程，
把测试平台加入 `sim_1`，再按测试平台开头的说明准备激励文件。

### `phy/sync/`：同步模块

```
python gen_stim.py 4 60 s4                 # 合成激励：随机半采样定时偏移、±36.6 kHz 频偏、AWGN，数据电平 −14 dBFS
python gen_replay.py <adc_edge.npz> board 48,46,45,44   # 板上实采采样回放（board/tests/adc_edge.py 的输出）
vivado -mode batch -source run.tcl -tclargs s4
python check_sync.py s4
```

`run.tcl` 第一次运行时在 `sim/phy/sync/prj/` 建工程（含 CORDIC、DDS、FIR Compiler IP），之后复用。`check_sync.py` 逐帧给出
`time_sync` 进入数据段的位置相对 LTF1 起点的偏差（+64 即锁到 LTF2）、未同步的帧、不对应任何帧的同步（误触发），合成激励还给出频偏估计误差。
结果（`results.txt`）：20 dB 下 20 帧全部定时正确，频偏误差均方根 1.1 kHz；4 dB 下 60 帧中 59 帧正确、1 帧帧检测漏检，频偏误差 12.5 kHz；
板上实采回放（TX 额外衰减 44～48 dB，共 36 帧）全部定在 LTF1，5 万个只含噪声的采样中没有误触发。

## `sscc/`：SSCC 基线

```
vivado -mode batch -source run_sim.tcl -tclargs "FRAMES:2 SNR:15.5 DUMP:1"
python sscc_ref.py
```

参数：`FRAMES` 帧数，`SNR` 信噪比（dB，省略为无噪声），`DUMP:1` 导出第一帧的发射字节与符号（`tx_bytes.txt`、`tx_syms.txt`）以及 LDPC
译码器的输入 LLR 与每个码字的输出（`dec_llr.txt`、`dec_out.txt`）。`sscc_ref.py` 用 Python 参考（`src/model/ldpc_ref.py`，逐位模型）
核对：发射符号逐个比较；对 RTL 译码器收到的同一组 LLR 重新译码，比较译出比特、收敛标志与迭代次数。第一次运行在 `sim/sscc/prj/` 建工程，
每次运行都会重新编译。码表包 `ldpc_r23_pkg.sv` 由 `python src/model/ldpc_ref.py --gen-pkg` 从 Vivado LDPC IP 自带的码表生成（TX、RX 各一份）。

| 命令参数 | 结果（每帧 16 360 字节，101 个码字） |
|---|---|
| `"FRAMES:1 DUMP:1"` | 0 字节错误；32 768 个符号与 Python 参考逐个相同；101 个码字均 1 次迭代收敛，与参考一致 |
| `"FRAMES:2 SNR:15.5 DUMP:1"` | 0 字节错误；202 个码字与参考完全一致（平均 4.62 次迭代，最多 7 次） |
| `"FRAMES:2 SNR:14.5 DUMP:1"` | 54 字节错误（3 个码字未收敛）；202 个码字与参考完全一致（平均 7.39 次迭代，最多 16 次）；输入 FIFO 无溢出 |

记录见 `sscc/results.txt`。

上板后的端到端验证（空口 PSNR–SNR、帧率、分段时延）见 `board/tests/` 与 `data/measurements/`。
