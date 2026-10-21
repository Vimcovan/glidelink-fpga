# 已确认的报告插图

此目录保存图1-1、2-5、2-7、2-8、2-9、2-11、2-19、2-24、2-25、2-26、2-29、2-30的当前可编辑版本。
报告引用的PNG已直接替换在原路径，映射和SHA-256见`approved_figures.json`。

- SVG保留文字与矢量图元，draw.io文件可在diagrams.net中继续编辑。
- 图2-5的原生Visio文件已更新到`../visio/block_rtl.vsdx`，三个块均已居中。
- 图2-8使用编码器、解码器两张饼图与原(b)(c)信息组成的完整新版，由`../sim/resource_plan.py`按`data/model_shallow/manifest.json`生成。
- 图2-6、2-28、3-8保持原图。

`../visio/`中的旧PowerShell绘图脚本仍对应此前布局；重跑这些脚本会覆盖已确认的PNG。
后续修改应以本目录可编辑文件为准，导出到清单中的PNG路径后再运行`report/tools/build_docx.ps1`。
仅重新生成设计报告不需要重跑绘图脚本。
