# 大模型协作记录

本项目由参赛者与两个 AI 编程代理协作完成：Claude Code（Anthropic）与 Codex（OpenAI）。
参赛者提出目标和约束、做关键的技术判断，并负责所有物理操作（接线、上电、观察 LED / 屏幕 / 天线）；代理在本机与两块板卡上
读写代码、运行 Vivado / 仿真 / 板上程序、采集数据并出图。

| 文件 | 内容 |
|---|---|
| [`cases.md`](cases.md) | 17 个典型案例：背景、模型做了什么、用户做了什么、结果，含出处 |
| [`claude_codex/`](claude_codex/) | 两个代理之间的协作：Claude Code 写给 Codex 的任务书、Codex 的网络章节原稿与合稿后版本、Codex 对全文的交叉审查及逐条处理 |
| [`transcripts/`](transcripts/) | 会话全文的压缩、脱敏版本：`claude/`（10 个会话）、`codex/`（30 个会话），文件名为 工作区_时间_会话 ID（见下） |
| [`tools/export_transcripts.py`](tools/export_transcripts.py) | 从 Claude Code / Codex 的原始 jsonl 记录生成上述转写的脚本 |

## 分工与时间线

| 时间（2026） | 工作 | 代理 |
|---|---|---|
| 09-01 – 09-07 | DeepJSCC 原型（连续 I/Q） | Codex |
| 09-12 | 802.11a 类 OFDM 发射机工程 | Codex |
| 09-24 – 09-26 | DeepJSCC-Q 按期刊版重写、训练（修正量化器错误）、PTQ 起步 | Codex |
| 09-25 | AD9361 纯 PL 配置调试（SPI 握手、TX 正交校准、FIR） | Claude Code |
| 09-26 – 09-28 | OFDM 自环 → 两板空口、AGC 配置、定时门限、长帧 SFO 跟踪、遥测 GUI | Claude Code |
| 09-28 – 09-29 | DeepJSCC-Q 训练（长训 1200 轮）、定点方案、存储规划、行缓存与卷积引擎 RTL 起步 | Claude Code |
| 09-29 – 09-30 | 网络 RTL 生成器完成、FIFO 定尺寸、比特精确验证、编 / 解码器 Vivado 工程 | Codex |
| 09-30 | PHY 与 DeepJSCC-Q 对接方案、PN 符号翻转、帧缓存 | Claude Code |
| 10-02 – 10-05 | PYNQ 上板：PS-PL 通路、端到端图像、摄像头、GUI、触摸屏、Mali-400 GPU、SSCC 基线 | Claude Code |
| 10-02 – 10-06 | 网络部分论文草稿（PPA 分析、证据链） | Codex |
| 10-05 – 10-06 | 演示视频（代理控制两块板卡、配音、剪辑）、AGC 现场记录、显示杂散排查 | Claude Code |
| 10-07 | AGC 看门狗与锁定电平、PSNR–SNR / 帧率 / 分段时延实测、开源仓库、设计报告 | Claude Code |
| 10-07 | 设计报告网络章节起草、全文交叉审查 | Codex（由 Claude Code 调用） |
| 10-07 – 10-11 | 设计报告：撰写、绘图，参赛者逐条批注修改 | Claude Code |
| 10-15 – 10-19 | 浅层解码器：结构探索、联合训练与量化感知训练、PWL 的 RTL、逐比特仿真、消融实验、上板测试与报告修改 | Claude Code |
| 10-20 – 10-21 | 残差块个数扫描与对照实验；SSCC 基线改用 LDPC（AWGN 仿真、Vivado LDPC IP 资源评估、编译码器 RTL 与逐位参考模型）；定时同步改为两点度量、频偏估计改为 64 点累加；重新上板测试 | Claude Code |

## 会话统计（与本项目相关的部分）

| 代理 | 工作区 | 会话 | 用户消息 | 代理回复段 | 时间 |
|---|---|---|---|---|---|
| Claude Code | AD9361 / PHY / 系统 | 5 | 293 | 1077 | 09-25 – 10-07 |
| Claude Code | DeepJSCC-Q / 仓库 / 报告 | 5 | 127 | 485 | 09-28 – 10-07 |
| Codex | DeepJSCC 原型 | 8 | 122 | 492 | 09-01 – 09-07 |
| Codex | DeepJSCC-Q 训练 | 1 | 68 | 247 | 09-24 – 09-26 |
| Codex | DeepJSCC-Q-FPGA（RTL、论文） | 6 | 139 | 372 | 09-29 – 10-06 |
| Codex | OFDM 发射机 | 1 | 3 | 8 | 09-12 |
| Codex | 本仓库（由 Claude Code 调用，含其子代理） | 14 | 14 | 28 | 10-07 |
| **合计** | | **40** | **766** | **2709** | |

统计截至 2026-10-07。Codex 的自动审批守护会话、定时唤醒消息、空会话，与项目无关的会话（如 Claude Code 账号迁移的咨询）和请求不计入。

## 转写的生成与脱敏

```
python tools/export_transcripts.py claude-batch transcripts/claude <~/.claude/projects/<项目>/*.jsonl ...>
python tools/export_transcripts.py codex-batch  transcripts/codex  <~/.codex/sessions> <标签>=<工作目录> ...
```

- **保留**：用户消息、代理的文字回复、每次工具调用一行（工具名 + 简述，折叠显示）。
- **去掉**：工具输出、系统 / 开发者提示、隐藏的推理过程、图片、账号 ID；续接会话中重复的历史只保留一次。
- **脱敏**：邮箱、IPv4 地址、本机用户目录与盘符路径（替换为 `<email>`、`<ip>`、`~`、`<work>` 等）；用户消息中的粗话替换为 `***`，与项目无关的请求连同回答整轮删除。其余内容保持原样（包括对 AI 的批评）。
- 原始记录约 740 MB（Claude Code 约 410 MB、Codex 约 330 MB），转写后约 3 MB。
