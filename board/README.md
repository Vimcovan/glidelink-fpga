# 上板

## 硬件与连接

| 部分 | 型号 / 设置 |
|---|---|
| 板卡 | 2 × PYNQ-ZU（xczu5eg-sfvc784-2-e），PYNQ 镜像 |
| 射频 | 2 × AD-FMCOMMS3-EBZ（AD9361），插在 PYNQ-ZU 的 FMC 上；LVDS 1R1T，FDD，20 MSPS，参考时钟 40 MHz |
| TX 外设 | USB UVC 摄像头（可选，否则用演示视频 / 预设图） |
| RX 外设 | 1024×600 触摸屏（DisplayPort + USB 触摸） |
| 网络 | PC ↔ RX 板 `192.168.3.1`；RX ↔ TX 板间网线：TX `192.168.4.1`、RX 侧 `192.168.4.2`（GUI 显示 TX 原图与 PSNR、调 TX 衰减用） |

上电顺序无要求。上电约 1 分钟后三个服务起来，触摸屏显示 GUI。

## 比特流

`bitstreams/jscc_tx.{bit,hwh}`、`jscc_rx.{bit,hwh}`：部署版（`SHA256SUMS` 与板上一致）。从源码重新生成见 `build/build_hw.tcl`。

## 服务（`src/sw/systemd/`）

| 板 | 服务 | 作用 |
|---|---|---|
| TX | `jscc-tx` | `tx_camera.py`：取图（摄像头 / 演示视频 / 预设图）→ 编码器；UDP 5006 接收模式、信源、衰减命令，发送预览；HTTP 8081 为手机提供原图 MJPEG 流（`/tx.mjpg`） |
| RX | `jscc-rx` | `rx_server.py`：下载比特流、PS 初始化 AD9361、AGC 看门狗；UDP 5005 发送解码图、遥测、频谱、SSCC 包 |
| RX | `jscc-web` | `web_server.py`：手机 / 浏览器控制页（端口 8080，经 WiFi 访问）：RX 重建图（`/rx.mjpg`，TX 原图由页面直接从 TX 板 8081 端口取）、帧率 / EVM、TX 衰减 / 模式 / 信源控制、星座 / 逐子载波 SNR / 频谱；在 WiFi 子网广播 `JSCC-STAT` 找到 TX 板，命令直接发给它；连着板间网线时 PSNR、时延与命令经 GUI 的远程端口（5010），与触摸屏同步 |
| RX | `jscc-gui` | `jscc_gui.py --lite --remote-port 5010`：触摸屏 GUI，`--lite` 为板载小屏模式（紧凑排版、降低刷新率、用 Mali-400 渲染 \|H\| 三维图；TX 预览、PSNR 与时延只在两板连网线时显示）；“手机”页显示手机控制页的地址和二维码（`--web-port`，默认 8080）（启动前 `display_mode.sh` 把显示像素时钟设为 52.01 MHz，避开 921.6 MHz 带内杂散） |

日志：TX `tx_camera.log`；RX `rx_server.log`（每 5 s 一行：帧率、PHY 计数、AGC 增益）、`agc_events.jsonl`（每次失步时的 AGC 状态快照，每行一条 JSON）、`gui.log`。

## 测量脚本（`tests/`，在 RX 板上运行，除注明外）

| 脚本 | 用途 |
|---|---|
| `measure_link.py` | PSNR / 星座 SNR / EVM / SSCC 帧成功率随 TX 衰减扫描；`--top` 先到最大衰减再逐点降低（AGC 只需跟随变强的信号）；`--save-const` 保存星座快照 |
| `analyze_const.py`（PC） | 固定图像下以参考星座计算数据辅助 SNR（不依赖判决） |
| `plot_link.py`（PC）、`plot_visual_compare.py`（PC）、`plot_psnr_snr.m`（MATLAB，英文版，用于海报） | 出图（`plot_visual_compare.py`：原图 / DeepJSCC-Q / SSCC 视觉对比，PSNR / SSIM） |
| `pl_events.py`（两板）+ `seg_latency.py`（PC）+ `plot_seg_cdf.m`（英文版，用于海报） | 两板 PS 轮询 PL 帧计数器打时间戳，按数据流配对：编码 / 传输 / 解码三段时延 |
| `frames_to_csv.py` | 逐帧帧率 |
| `power_rails.py` | 板载 PMBus 遥测（三片 IRPS5401）读各电源轨电压、电流、功率，按时间平均；PL / PS / DDR 分组 |
| `agc_guard_test.py` | AGC 看门狗验收：静置误复位、衰减跳变恢复、无信号恢复 |
| `agc_trace.py`、`adc_frames.py`、`pl_counters.py` | 诊断：原始 ADC 抓取与 AD9361 `CTRL_OUT`（状态输出引脚，此处为 AGC 状态）同步记录、帧间隔、PL 计数器 |
| `adc_edge.py` | 诊断：链路边缘处各衰减点的原始 ADC 采样（含 TX 衰减最大时的噪声底）、同步率与增益，供离线同步分析和 `sim/phy/sync/` 的 RTL 回放 |
| `agc_edge_probe.py` | 诊断：在线改写一个快速 AGC 寄存器（如 0x110、0x118），比较各衰减点的同步率、解码帧率与增益，结束时恢复原值 |
| `lvds_loopback.py` | 换射频子卡后的检查：AD9361 数据口环回（不经射频），测出子卡上 P/N 对调的 TX 数据线对，给出该卡对应的 SW0 位置；`lvds_invert_scan.py`、`lvds_rx_group.py` 为测定 LVDS 反相寄存器各位含义时用的诊断脚本 |
| `tx_spi.py`（TX 板） | 在 TX 服务运行时读写一个 AD9361 寄存器 |

## 常见问题

- **AGC 卡在低增益、衰减加大后失步**：快速 AGC 在 20 MSPS 下偶发不解锁；`rx_server` 的看门狗在同步率 < 5 帧/s 持续 0.5 s 且增益未到最大时复位 AGC（约 1–3 s 恢复）。
- **SSCC 在高 SNR 下也偶发整帧失败**：已通过 AGC 锁定电平 −14 dBFS 解决（见 `THIRD_PARTY.md` 末节）；若改动初始化脚本后复现，用 `tests/agc_trace.py --adc` 检查帧内增益台阶。
- **启动时的告警**：服务启动后的十几秒内 `rx_server.log` 中的 `spectrum: ADC capture did not complete` 与 0 帧/s 属正常，AGC 锁定后消失；`gui.log` 中的 `libGL error: failed to load driver: xlnx` 为 Mesa 找不到 Xilinx 专用驱动后的正常回退，随后出现 `h3d_render: Mali400` 即三维渲染正常。
- **抓取 ADC / 测频谱前**先 `systemctl stop jscc-rx`（与频谱线程共用抓取 DMA）；`rx_server` 停止时会复位两个 DMA，防止遗留传输卡住下一个进程。
- **换射频子卡后 EVM 只有约 −6 dB、能同步但解不出图**：子卡上 TX 数据线对的 P/N 走线可能与开关位置不符。两块板的拨码开关 SW0 选择 TX 线对极性：拨下（0）用于 FMCOMMS3（无对调，实测两块均如此），拨上（1）用于此前使用的第三方 AD9361 子卡（D5 线对对调，RTL 对样点的 bit 11 和 bit 5 取反，`ad9361_lvds_mode.v` 的 `TX_SWAP_PAIRS`）。开关为静态输入，拨动后立即生效，无需重新加载比特流；当前位置可从 `STATUS` 寄存器 bit 1 读出（`tx_camera.log` 启动时也会打印）。换上不确定的子卡时，把它插到 RX 板上运行 `tests/lvds_loopback.py`，脚本给出该卡对应的开关位置。
- **带内杂散**：显示像素时钟谐波（51.2 MHz × 18 = 921.6 MHz）落入 RX 带内，已改用 52.01 MHz；40 MHz 参考时钟的 23 次谐波（920 MHz，子载波 +16）为 RX 板侧固有杂散。
