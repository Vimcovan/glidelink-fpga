# DeepJSCC-Q 模型：训练、量化导出、链路评估

本目录是板上运行的神经网络的软件部分：浮点模型定义与训练、W8A12 整数量化与导出（供 `src/network/` 生成 RTL），
以及 OFDM 链路模型下的评估。板上运行的是 DeepJSCC-Q 编码器 + 浅层解码器（第 4 节，`data/model_shallow/`）；
第 1–3 节的对称模型（`data/model/`）是它的第一段训练，其编码器是浅层解码器联合训练的起点。

## 1. 原模型：DeepJSCC-Q

模型来自 Tung、Kurka、Jankowski、Gündüz 的 DeepJSCC-Q（IEEE JSAIT 2022，报告参考文献 [2]）。它把图像直接映射为
有限星座上的信道符号，因此可以接在标准数字调制的物理层后面，不需要再做压缩和纠错编码。

- 编码器 / 解码器（论文 Fig. 3）：沿用 Cheng 等人（CVPR 2020）的图像压缩网络结构，由带步长的残差块（含 GDN）、
  普通残差块、注意力块组成；解码器对称，用 PixelShuffle 上采样、IGDN 归一化。原网络主通道数 256，
  编码器有 4 个步长为 2 的残差块，总下采样 16 倍，256×256 图像得到 16×16×256 的潜变量。
- 星座量化：编码器输出的实数潜变量两两配成复数 (I, Q)，前向取最近的星座点（硬量化）；反向用到各星座点距离的
  softmax 加权平均（软量化）传梯度。softmax 的温度 σ_q 从 5 开始，每 10 000 次参数更新加 5，上限 100，
  使软量化逐渐逼近硬量化。
- 损失：重构 MSE + λ·KL(p‖u)，p 是批内软分配得到的星座点使用概率，u 是均匀分布。KL 项让 64 个星座点被均匀使用，
  从而发射功率稳定在星座的平均功率上。
- 信道：AWGN，接收端把带噪的连续 I/Q 直接送进解码器，不做星座判决。
- 论文的训练设置：ImageNet 128×128 随机裁剪，Adam（学习率 1×10⁻⁴，β2 = 0.99），批大小 32，
  验证损失连续 4 轮不降则学习率乘 0.8；在 Kodak 上测试。

## 2. 本作品的修改

目标是在单片 xczu5eg 上以 30 fps 运行编码器和解码器，全部权重放在片上存储中。保持每幅图的信道符号数不变，
只缩小网络：

| 项 | 原网络 | 本作品 | 原因 |
|---|---|---|---|
| 主通道数 / 潜变量通道数 | 256 / 256 | 32 / 16 | 权重和乘加量随通道数平方增长，原网络编码器 8 位权重就有 10.6 MB，片上存储只有约 2.95 MB |
| 下采样 | 4 个步长 2 的残差块，16 倍 | 前 2 个步长 2、后 2 个步长 1，4 倍 | 通道减少后增大潜变量空间尺寸 |
| 解码器上采样 | 4 个上采样块各 2 倍 | 前 2 个 1 倍、后 2 个 2 倍 | 与编码器对称 |
| 潜变量 | 16×16×256 | 64×64×16 | 都是 65 536 个实数 = 32 768 个 64QAM 符号，信道使用量相同 |
| 星座 | 固定 64QAM 或可学习星座 | 固定 64QAM（单位平均功率） | 物理层用标准 64QAM |
| LeakyReLU 斜率 | 未注明 | 1/128 | 硬件中负半轴只需右移 7 位 |
| 数值精度 | 浮点 | 训练后量化为 W8A12（8 位权重、12 位激活） | 片上存储与 DSP 位宽 |

残差块、GDN / IGDN、注意力块、PixelShuffle、软硬量化与 KL 正则都与原网络相同。缩小后参数量 387 288 个，
每帧乘加量编码器 0.913 G、解码器 1.417 G，约为原网络的 1/51（`arch_compare.py` 计算）。

另外，潜变量按硬件数据流的顺序配对：沿 64×64 像素逐个取出，每个像素的 16 个通道依次两两组成 (I, Q)，
得到 8 个符号；训练中的配对与板上完全一致。

## 3. 训练流程

1. 数据：DIV2K 训练集 800 张 + Flickr2K 2650 张，共 3450 张 2K 分辨率图像；DIV2K 验证集 100 张取中心 256×256
   作验证集。图像预先解码保存为原始像素数组，训练时每轮从每张图随机裁一块 256×256，随机水平 / 垂直翻转。
2. 训练条件：AWGN 信道，SNR 10 dB；损失 MSE + 0.05·KL；Adam（学习率 2×10⁻⁴，β2 = 0.999）；
   每 8 张图一个小批次，累积 2 个小批次更新一次参数（有效批大小 16）；混合精度。
3. 两段训练：
   - 第 1–200 轮学习率固定为 2×10⁻⁴；
   - 第 201–1200 轮从第 200 轮的状态续训，验证集 MSE 连续 20 轮不降则学习率乘 0.8，到第 1200 轮降为 1.7×10⁻⁵。
4. σ_q 按小批次计步（每 10 000 个小批次加 5），约第 440 轮到达上限 100。
5. 每轮结束在验证集上计算 PSNR，保留 PSNR 最高的一轮作为部署模型（第 1186 轮）。

与论文设置不同的原因：本作品的训练集只有 3450 张图，按论文的批大小 32 和学习率 1×10⁻⁴，每轮只有约 100 次
参数更新，收敛很慢；改为更小的批、更大的学习率，后期再降学习率，放宽到 20 轮不降才衰减，以适应每轮
验证结果的波动。

部署模型（浮点，AWGN 10 dB）：

| 评估集 | PSNR |
|---|---|
| DIV2K 验证集（100 张，256×256 中心裁剪） | 31.28 dB |
| Kodak（24 张，原分辨率；全部像素合并计算 MSE） | 32.65 dB |
| Kodak（逐图 PSNR 的平均，每张 10 次噪声，与论文 Fig. 12 同口径） | 33.26 dB |

平均发射功率 1.00，星座熵 6.00 bit（64 个点基本均匀使用）。论文在相同工作点（Kodak、64QAM、λ = 0.05、
训练与测试 SNR 均为 10 dB、带宽比 1/6）上的原网络约为 34.9 dB，差距来自网络缩小和训练数据规模（ImageNet 约 120 万张）。

## 4. 浅层解码器（部署版）

第 3 节的解码器与编码器对称，每帧 1.417 GMAC，是整个系统中最大的模块。部署版把解码器换成非对称的浅层结构
（`train_shallow_dec.py --subpixel --k1 3 --n 24 --k2 3 --pre 4 --pre-act pwl --pre-lin 1`）：

- 64×64 上：线性 3×3 卷积（16→16）+ 4 个残差块（3×3 卷积 → PWL → 3×3 卷积 → PWL，加恒等支路）；PWL 为每通道
  可学习的分段线性激活，9 个折点均匀分布在 [−2, 2]，以 LeakyReLU 初始化；
- 第一级上采样：3×3 卷积 16→192（主路与线性旁路各 96 通道）+ PixelShuffle，主路接 IGDN，两路相加；
- 第二级上采样：3×3 卷积 24→12 + PixelShuffle + sigmoid。

每帧 0.250 GMAC（卷积 0.241 GMAC），参数 53 332 个。设计依据（Yang 与 Mandt 的非对称思路、直线插值的线性程度、
消融实验）见 `report/notes/shallow_decoder_design.md`，分析脚本在 `shallow_analysis/`。

训练（`train_shallow_dec.py`，`--encoder` 默认取 `data/model/checkpoint/best.pt`）：

1. 冻结编码器，以无噪声的接收符号训练解码器 300 轮（学习率 1×10⁻³）；
2. 编码器与解码器在 10 dB AWGN 下联合训练 800 轮（`--joint`，解码器 3×10⁻⁴、编码器 1×10⁻⁴，验证集 PSNR 30 轮
   不提高时减半，KL 权重 0.05），再续训 600 轮（`--resume-joint`）；
3. 8 位权重的量化感知训练 200 轮（`--qat-wbits 8`），第 172 轮为部署检查点。

各段的每轮记录在 `data/model_shallow/checkpoint/training_{clean,joint,cont,qat}.csv`。部署模型（AWGN 10 dB）：
DIV2K 验证集浮点 31.13 dB、W8A12 31.13 dB；Kodak 浮点 32.61 dB、W8A12 32.58 dB（逐图平均 33.24 dB）。
与对称解码器相比，W8A12 整数模型在 10 dB 下低 0.09 dB（DIV2K）/ 0.01 dB（Kodak）。联合训练后编码器星座熵 6.00 bit、
平均功率 1.00。

## 5. 量化导出

对称模型（`export_fpga.py`）为训练后量化：

- 权重：每个输出通道一个缩放系数，量化为 8 位整数；
- 激活：每个张量一个缩放系数，量化为 12 位整数。缩放系数由 64 张训练图（经 10 dB AWGN）上统计的最大绝对值
  乘 1.5 倍余量得到；
- 整数参考模型规定了偏置、舍入、饱和、GDN 开方与除法、sigmoid 分段线性近似等全部整数运算，RTL 与它逐比特一致。

量化损失：DIV2K 验证集 0.052 dB，Kodak 0.062 dB。

部署模型（`quant_shallow.py`、`export_shallow.py`）沿用同一 W8A12 规则，编码器部分直接使用 `int_ref.py` 的整数实现；
PWL 输入的定标取 2 的幂次，使折点落在整数上、段宽为 2 的幂次，每通道存 8 位斜率与折点纵坐标：
acc = Y[c][k] + S[c][s]·(x − T0 − k·2^L)，之后接与卷积相同的重定标。量化损失：DIV2K 0.002 dB，Kodak 0.027 dB。

## 6. 文件

| 文件 | 作用 |
|---|---|
| `deepjsccq_model.py` | 浮点模型：编码器、解码器、64QAM 软硬量化、AWGN 信道 |
| `prepare_cache.py` | 把训练图像解码保存为原始像素数组，验证集保存中心 256×256 裁剪 |
| `train_v2.py` | 训练，记录每轮的训练 / 验证指标，保存最新和最佳模型 |
| `int_ref.py` | 整数参考模型（RTL 的逐比特参考，也用于 `sim/network/`） |
| `ptq_int16.py` | 量化校准与浮点 / 整数对比评估（由 `export_fpga.py` 调用） |
| `export_fpga.py` | 导出 W8A12 整数模型：各层参数文件、网络描述文件、各层中间结果的参考数据 |
| `arch_compare.py` | 计算原网络与本作品网络的参数量和乘加量 |
| `eval_kodak_per_image.py` | Kodak 逐图 PSNR 评估 |
| `train_shallow_dec.py` | 浅层解码器的训练（冻结编码器 / 联合训练 / 量化感知训练）与 SNR 扫描 |
| `quant_shallow.py` | 浅层解码器的 W8A12 整数模型（含 PWL）与浮点 / 整数对比评估 |
| `export_shallow.py` | 导出部署模型（编码器 + 浅层解码器）：参数文件、网络描述文件、参考数据 |
| `shallow_analysis/` | 浅层解码器的分析与报告出图：插值线性度、各级噪声、消融、Yang 与 Mandt 的大核 |
| `ofdm_channel.py`、`ofdm_eval.py` | 类 802.11a OFDM 链路模型下的评估（PAPR、限幅、频率选择性信道、LTF 信道估计、ZF / MMSE 均衡） |
| `bench_gpu.py` | GPU 对照测试：部署网络在 FP32 / FP16 / W8A12 伪量化下的延迟、吞吐率和每帧能耗（结果在 `data/measurements/gpu_bench/`） |
| `download_flickr2k.ps1` | 下载 Flickr2K |

`data/model_shallow/` 中是部署模型（编码器 + 浅层解码器）的数据，`data/model/` 中是对称模型的数据，结构相同：

- `checkpoint/`：浮点模型（`best.pt`）、两段训练的命令行参数、每轮训练曲线和最终评估结果；
- `manifest.json`：网络描述文件，包括每层的类型、张量形状、量化参数和片上存储规划，RTL 生成器据此生成各层；
- `params/`：各层的整数权重、偏置和重定标参数；`rtl_init/`：由它们生成的 RTL 存储初始化文件。

## 7. 复现

```
# 1. 数据：DIV2K train/valid（https://data.vision.ee.ethz.ch/cvl/DIV2K/）、Flickr2K（download_flickr2k.ps1）、
#    Kodak 24 张（kodim*.png，放在 data/Kodak，训练结束时评估用）
python prepare_cache.py --train-dirs <DIV2K_train_HR> <Flickr2K> --val-dir <DIV2K_valid_HR> --out data/cache
# 2. 训练：第一段 200 轮，第二段续训到 1200 轮并启用学习率衰减
python train_v2.py --cache data/cache --output-dir runs/deploy --paper-arch --kl-lambda 0.05 --epochs 200 --channels-last
python train_v2.py --cache data/cache --output-dir runs/deploy --paper-arch --kl-lambda 0.05 --epochs 1200 \
       --plateau --plateau-patience 20 --channels-last --resume runs/deploy/latest.pt
# 3. 量化导出（W8A12），输出即 data/model/ 中的 manifest.json 与 params/
python export_fpga.py --checkpoint runs/deploy/best.pt --out-dir runs/fpga_export --wbits 8 --abits 12
# 4. 浅层解码器：冻结编码器的无噪声训练、联合训练、续训、量化感知训练，再导出到 data/model_shallow
S="--subpixel --k1 3 --n 24 --k2 3 --pre 4 --pre-act pwl --pre-lin 1 --cache data/cache --kodak data/Kodak"
python train_shallow_dec.py $S --output-dir runs/clean --epochs 300 --snr-db inf --lr 1e-3
python train_shallow_dec.py $S --output-dir runs/joint --epochs 800 --plateau-patience 30 --snr-db 10 --joint \
       --enc-lr 1e-4 --kl-lambda 0.05 --lr 3e-4 --init-decoder runs/clean/best.pt
python train_shallow_dec.py $S --output-dir runs/cont --epochs 600 --plateau-patience 30 --snr-db 10 --joint \
       --enc-lr 5e-5 --lr 1.5e-4 --resume-joint runs/joint/best.pt
python train_shallow_dec.py $S --output-dir runs/qat --epochs 200 --plateau-patience 20 --snr-db 10 --joint \
       --enc-lr 2.5e-5 --lr 7.5e-5 --qat-wbits 8 --resume-joint runs/cont/best.pt
python export_shallow.py --ckpt runs/qat/best.pt --cache data/cache --kodak data/Kodak
# 5. 生成 RTL：src/network/regen_hw.py（默认读 data/model_shallow，见根目录 README）
```

不重新训练时，直接从仓库中的 `data/model_shallow/`（部署检查点与它的量化导出）开始：`src/network/regen_hw.py` 重新生成 RTL，`sim/network/make_golden.py` 生成仿真参考数据，只需要 NumPy 与 Pillow。第 3、4 步需要 CUDA 和第 1 步的校准图像。

`--paper-arch` 选用论文的模块定义（GDN 参数化方式、注意力块内的残差连接、残差块的激活位置）。

工具：Python 3 + PyTorch（CUDA）+ NumPy + Pillow。
