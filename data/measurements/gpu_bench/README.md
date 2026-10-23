# GPU 对照测试

部署网络（联合训练的编码器 + 浅层解码器）在桌面 GPU 上的延迟、吞吐率和每帧能耗，用于和 FPGA 实现对照。
测试脚本：`src/model/bench_gpu.py`；结果：`rtx5060ti.json`。

```
cd src/model
python bench_gpu.py --cache <data/cache> --out ../../data/measurements/gpu_bench/rtx5060ti.json
```

## 测试方法

GPU 没有 8 位权重 × 12 位激活的整数运算单元，网络按 GPU 自身的精度运行：

| 精度 | 说明 |
|---|---|
| FP32 | 单精度，关闭 TF32 |
| FP16 | 半精度权重与激活 |
| W8A12 伪量化 | FP32 运算；权重按输出通道量化为 8 位，每层卷积输入按标定范围量化为 12 位。数值类别与 FPGA 相同，但不逐比特一致（逐比特参考是 `src/model/int_ref.py`） |

- 编码器：uint8 图像 → 64QAM 符号（硬判决）；解码器：符号 → uint8 图像。图像在 GPU 上，不含 PCIe 拷贝。
- 延迟：batch 1，CUDA graph，300 次取中位数和 p99。
- 吞吐率：batch 1～64，各运行 2 s。
- 功耗：nvidia-smi 每 100 ms 读一次 `power.draw.instant`（显卡板载功耗，不含主机 CPU 与内存），分别测建立 CUDA 上下文后的空闲功耗、按 30 fps 定速运行（batch 1，与 FPGA 相同的工作点）20 s、满负荷运行（吞吐率最高的 batch）20 s。
- 画质检查：DIV2K 验证集 100 幅中心裁剪，10 dB AWGN。

## 结果（RTX 5060 Ti，torch 2.9.0 + CUDA 12.8，2026-10-23）

建立 CUDA 上下文后的空闲功耗 14.8 W。

| 精度 | PSNR（10 dB） | batch 1 延迟：编码 / 解码 / 合计 | 最大吞吐率 | 满负荷功耗、每帧能耗 | 30 fps 功耗、每帧能耗 | 30 fps 每帧动态能耗 |
|---|---|---|---|---|---|---|
| FP32 | 32.57 dB | 1.02 / 0.45 / 1.48 ms | 982 fps（batch 4） | 179 W，182 mJ | 21.2 W，708 mJ | 215 mJ |
| FP16 | 32.57 dB | 0.48 / 0.31 / 0.78 ms | 2634 fps（batch 8） | 180 W，68 mJ | 16.9 W，563 mJ | 70 mJ |
| W8A12 伪量化 | 32.53 dB | 1.31 / 0.52 / 1.84 ms | 827 fps（batch 4） | 173 W，209 mJ | 21.4 W，712 mJ | 218 mJ |

动态能耗 = (功耗 − 空闲功耗) / 帧率。

## 与 FPGA 对照（30 fps）

| | FPGA（本作品） | GPU（FP16） |
|---|---|---|
| 延迟 | 编码 38.2 ms、解码 26.4 ms（PL 内，各层并行度按 30 fps 取最小值） | 0.78 ms |
| 吞吐率 | 30 fps（设计预算） | 最高 2634 fps |
| 神经网络动态能耗 | 编码器 10.7 mJ/帧，解码器 ≤ 13.3 mJ/帧 | 70 mJ/帧（编码 + 解码） |
| 整体功耗 | 两块板 6.51 W，217 mJ/帧（含 OFDM 物理层、PS、DDR） | 显卡 16.9 W，563 mJ/帧（不含主机） |
| 动态能效 | 编码器约 86 GMAC/s/W | 满负荷约 18 GMAC/s/W |

FPGA 数据取自设计报告表 3-2：编码器为 TX 板 DeepJSCC-Q 与 SSCC 模式的 PL 内核功耗差 0.32 W；RX 板的 PL 内核
（0.40 W）同时包含解码器、OFDM 接收与 LDPC 译码器，故解码器只给出上限。编码器每帧 0.913 GMAC，30 fps 下为
27.4 GMAC/s。两者的数值精度（整数 W8A12 与浮点）和功耗测量方法（PMBus 电源轨与显卡板载传感器）不同。
