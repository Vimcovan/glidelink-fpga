# 第三方内容与来源说明

本仓库自研部分按 MIT 许可（见 `LICENSE`）。下列内容来自第三方，或参考了第三方的设计 / 数值，保留其原有许可。

| 内容 | 仓库中的位置 | 来源 | 许可 / 处理方式 |
|---|---|---|---|
| AD9361 LVDS 数据接口 | `src/hw/{tx,rx}/rtl/phy/ad9361_lvds_mode.v` | Analog Devices HDL（`analogdevicesinc/hdl`，如 hdl_2015_r1 的 `library/axi_ad9361/axi_ad9361_dev_if.v` 与 `library/common/ad_lvds_{in,out}.v`）→ lzk2211/Zedboard_AD9361_radar（合并为 `axi_ad9361_dev_if.v`，删去了 ADI 声明）→ 米联客（MiLianKe）AD9361 例程 → 本项目修改 | ADI 许可（BSD 类，Copyright 2011 Analog Devices；须保留声明，且仅用于连接 ADI 器件）；**已在文件头恢复 ADI 原始声明并注明修改链** |
| AD9361 SPI 读写与初始化控制 | `src/hw/{tx,rx}/rtl/phy/ad9361_spi.v`、`ad9361_config.v` | lzk2211/Zedboard_AD9361_radar（`ad9361_spi.v`、`ad9361_init.v`）→ 米联客例程 → 本项目修改（端口改名、修正空闲时重发上一条命令的错误、PS 命令接口） | **已获授权**：原作者 lzk2211 于 2026-10-07 在 [lzk2211/Zedboard_AD9361_radar#1](https://github.com/lzk2211/Zedboard_AD9361_radar/issues/1) 中同意本项目在 MIT 许可下再分发上述两个文件的修改版本；文件头已注明出处与授权 |
| AD9361 初始化寄存器序列 | `src/hw/{tx,rx}/rtl/phy/ad9361_config_lut.v`、`src/sw/rx/ad9361_rx_init.json`（由 `src/sw/tools/lut_to_json.py` 从前者转换） | Analog Devices AD9361 配置工具生成的寄存器序列 | 按 ADI 工具输出使用；寄存器含义见 ADI UG-570 / UG-671 |
| 快速 AGC 寄存器取值 | `ad9361_rx_init.json` 中 0x0FA、0x101–0x11B 等 | 参考 openwifi `agc_settings.sh` 的数值（0x101 锁定电平已改为 −14 dBFS，见下） | openwifi（AGPL-3.0）**未包含在本仓库**，只借用了寄存器数值并在此注明 |
| GDN 层的参数化方式 | `src/model/deepjsccq_model.py`（`gdn_param='compressai'`） | CompressAI（InterDigital） | BSD-3-Clause-Clear；本仓库为自行实现，未包含 CompressAI 代码 |
| DeepJSCC-Q 方法 | `src/model/` | Tung, Kurka, Jankowski, Gündüz, "DeepJSCC-Q: Constellation constrained deep joint source-channel coding", IEEE JSAIT 2022 | 论文方法的复现与 FPGA 化实现 |
| Vivado IP 配置 | `src/hw/{tx,rx}/ip/*/*.xci` | AMD / Xilinx Vivado IP（FFT、FIFO、DDS、CORDIC、除法器、Clocking Wizard、VIO 等） | 仅为 IP 配置文件，IP 本身随 Vivado 提供，受 AMD 许可约束 |
| PYNQ-ZU PS 配置 | `src/hw/ps_config_list.txt` | PYNQ-ZU 板卡的 Zynq UltraScale+ PS 预设 | 板卡厂商 / PYNQ 项目提供 |
| 二维码生成 | `src/sw/rx/qrcodegen.py` | Project Nayuki QR Code generator library（Python 版 1.8.0，PyPI `qrcodegen`），未修改；RX 触摸屏“手机”页用它显示控制页地址 | MIT（Copyright Project Nayuki），文件头保留原声明 |
| TX 演示视频 | `src/sw/tx/media/colorful_256.mp4` | 本项目提供 | 经确认可随本仓库（MIT）分发 |
| 测试图像 | 不包含 | DIV2K（`src/sw/tools/make_presets.py` 从 DIV2K valid 生成 256×256 预设图）、Kodak（kodim23） | 数据集不再分发，请从官方地址下载 |
| 训练数据 | 不包含 | Flickr2K / DIV2K（`src/model/download_flickr2k.ps1`） | 同上 |

## 对 openwifi AGC 配置的改动

`0x101`（AGC 锁定电平）由 openwifi 的 12（−12 dBFS）改为 14（−14 dBFS）：SSCC 基线没有星座熵约束，PAPR 尾部比
DeepJSCC-Q 高约 0.8 dB，在 −12 dBFS 工作点会触发大 ADC 过载，使锁定中的快速 AGC 帧内解锁、改变增益（实测台阶帧
9/45 → 0/44，数据辅助 SNR 不降反升 0.1–1.9 dB）。详见 `report/`。
