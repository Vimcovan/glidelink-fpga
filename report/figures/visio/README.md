# Visio 源图

报告与 README 中的示意图用 PowerShell 驱动 Microsoft Visio（COM）绘制，`.vsdx` 可直接在 Visio 中编辑。

| 脚本 | 输出 | 内容 |
|---|---|---|
| —（手绘） | `fig02_network.vsdx`、`../network/fig02_network.png` | 编码器 / 浅层解码器模块结构，各块输出尺寸与 `data/model_shallow/manifest.json` 一致 |
| `fig07_memory.ps1` | `fig07_memory.vsdx` | 旧版布局；报告图2-9现由 `../sim/resource_plan.py` 生成 |
| —（手绘） | `shallow_decoder.vsdx`、`../network/dec_structure.png`、`../../notes/figures/shallow_decoder/dec_structure.png` | 浅层解码器结构 |
| `design_flow.ps1` | `design_flow.vsdx`、`../design_flow.png` | 设计流程（报告1.6节） |
| —（手绘） | `sys_overview.vsdx`、`../hw/sys_overview.png` | 系统整体框图 |
| —（手绘） | `block_rtl.vsdx`、`../hw/block_rtl.png` | RG / UP / ATT 块的 RTL 组成 |
| `gdn_unit.ps1` | `../hw/gdn_unit.png` | GDN / IGDN 模块 |
| `tx_chain.ps1`、`rx_chain.ps1` | `../hw/tx_chain.png`、`../hw/rx_chain.png` | OFDM 发射链、接收链 |
| `frame_format.ps1` | `../hw/frame_format.png` | 帧格式与子载波分配 |
| —（手绘） | `sync_hw_fd.vsdx`、`../hw/fd_hw.png` | 帧检测的硬件结构 |
| `ts_hw.ps1` | `sync_hw_ts.vsdx`、`../hw/ts_hw.png` | LTF 定时的硬件结构（与帧检测图同一风格） |
| —（手绘） | `sfo_loop.vsdx`、`../hw/sfo_loop.png` | SFO / CPE 跟踪环 |
| `ad9361_ctrl.ps1` | `../hw/ad9361_ctrl.png` | SPI 总线归属与 TX 正交校准相位扫描 |
| `sw_arch.ps1`、`tx_flow.ps1`、`agc_flow.ps1`、`gpu_pipeline.ps1`、`pc_toolchain.ps1` | `../sw/*.png` | 软件架构与流程图 |
| `visio_style.ps1` | — | 公共样式（宋体 + Times New Roman，浅色填充，与 `fig05_conv_engine.png` 一致）和绘图函数 |

```
powershell -ExecutionPolicy Bypass -File fig07_memory.ps1
```

PNG 按 300 dpi 导出。脚本含中文，须保存为带 BOM 的 UTF-8（Windows PowerShell 5.1）。
`fig05_conv_engine.vsdx`（卷积引擎）、`fig02_network.vsdx`（网络结构）、`block_rtl.vsdx`（块的 RTL 组成）、`sfo_loop.vsdx`（跟踪环）、`sync_hw_fd.vsdx`（帧检测）、`sys_overview.vsdx`（系统框图）与 `shallow_decoder.vsdx`（浅层解码器结构）为手绘图，不由脚本生成，在 Visio 中修改后另存 300 dpi 的 PNG。
