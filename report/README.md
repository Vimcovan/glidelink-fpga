# 设计报告与大模型协作记录

| 文件 / 目录 | 内容 |
|---|---|
| [`design_report.docx`](design_report.docx) | 设计报告（竞赛模板，公式为 MathType 对象）：由 `sections/` 生成后手工调整版式，再加上封面与目录（`tools/cover_toc.py`）；此后的修改直接在这份 docx 中进行 |
| [`design_report.pdf`](design_report.pdf) | 同一报告的 PDF（由 Word 从 docx 导出，含版面手工调整，带章节书签） |
| [`design_report.md`](design_report.md) | 同一报告的 Markdown 版本（`sections/*.md` 按顺序拼接；公式改写为 GitHub 的 ` ```math ` 与 `` $`…`$ `` 写法以便网页显示） |
| [`sections/`](sections/) | 报告源文件：摘要、术语与缩略语、作品概述、系统组成及功能说明（硬件 / 软件）、完成情况及性能参数、总结、参考文献、附录 |
| [`tools/`](tools/) | 生成 docx：`build_docx.ps1`（pandoc + 竞赛模板）、`make_reference_docx.py`（由模板生成样式表）、`docx_filter.lua`（图题 / 表题样式）、`math_markers.lua` + `mathtype_convert.ps1`（公式转 MathType）、`md_github.py`（生成 GitHub 显示用的 Markdown）、`cover_toc.py`（在已排版的 docx 前加封面与目录） |
| [`figures/`](figures/) | 报告用图：`hw/`、`sw/`（Visio 绘制）、`network/`（神经网络）、`results/`（实测与截图）、`sim/`（Python 作图脚本）、`visio/`（Visio 源图与脚本）、`redraw/`（SVG / draw.io 源图，用 Edge 渲染） |
| [`notes/`](notes/) | 开发过程记录：AD9361 调试、PHY 与 DeepJSCC-Q 对接、AGC、浅层解码器的设计与实验、SSCC 基线的 LDPC 设计 |
| [`poster/`](poster/) | 英文海报（A0 竖版，`poster_en.pptx` + 预览图）；按 AMD 海报模板制作的中、英文海报（`poster_amd*`，生成脚本在 `poster/amd/`） |
| [`llm_collab/`](llm_collab/) | 大模型协作记录：典型案例、Claude Code 与 Codex 的协作材料、会话转写与生成脚本 |

## 生成 docx

```
powershell -ExecutionPolicy Bypass -File report/tools/build_docx.ps1 -Template <竞赛设计报告模板.docx> -Pandoc <pandoc.exe> -OutName design_report.docx -MathType
```

需要 pandoc（3.11 测试通过）；`-MathType` 另需 Windows 上的 Word 与 MathType 桌面版（含 Word 插件），运行前须关闭 Word。
不加 `-MathType` 时公式为 Word 原生公式。竞赛模板不随仓库分发。输出在 `report/build/`（不入库）。
重新生成得到的是未经手工排版、没有封面和目录的版本，不能直接覆盖 `design_report.docx`。
