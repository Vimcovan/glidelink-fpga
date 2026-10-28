# 外壳

TX、RX 两块板共用的外壳：3D 打印底壳 + 3 mm 透明亚克力顶盖（设计说明见设计报告 2.2.2 节）。

| 文件 | 内容 |
|---|---|
| `enclosure.py` | 参数化外壳（构造实体几何）：板卡实测尺寸、接口开孔、散热孔、立柱都在文件开头的参数中；生成下面 `out/` 中的文件 |
| `render.py` | 底壳与简化板卡实体的渲染图（检查装配） |
| `report_fig.py` | 报告图 2-2（`report/figures/hw/enclosure.png`） |
| `print_templates.ps1` | 按实际尺寸打印侧板纸样（不经 PDF 阅读器缩放），用于在实物上核对开孔 |
| `out/enclosure_base.stl` | 底壳，193 × 209.4 × 43.5 mm，3D 打印（光敏树脂或尼龙 PA12），每台 1 个 |
| `out/lid_plain.dxf` | 顶盖，3 mm 透明亚克力激光切割（外形、4 个 Ø3.4 螺丝孔、55 个 Ø5 散热孔）；`lid_TX.dxf` / `lid_RX.dxf` 另带刻字图层 |
| `out/wall_sheet.pdf` | 四个侧板开孔的 1:1 纸样（A4） |

```
pip install manifold3d trimesh ezdxf numpy matplotlib pillow
python enclosure.py          # out/*.stl, *.dxf, 纸样
python report_fig.py         # 报告插图
```

## 配件（每台）

| 配件 | 数量 | 用途 |
|---|---|---|
| M3 × 8 mm 双通铜柱 | 4 | 开发板与底壳之间（板卡背面元件最高 5.35 mm） |
| M3 × 6 mm 螺丝 | 8 | 铜柱上下两端 |
| M3 × 8 mm 螺丝 | 4 | 顶盖固定到四角立柱（立柱孔 Ø2.6，直接攻入） |
| Ø12 mm × 4 mm 硅胶脚垫 | 4 | 底面凹槽 |

装配：铜柱从底壳下方固定 → 子卡插在开发板上，整体竖直放入底壳，SMA 落入前侧板的通槽 → 开发板拧到铜柱上 → 接天线和线缆 → 盖上顶盖。

## 贴纸

`logo/` 为外壳贴纸的 GlideLink 标志：圆形（Ø50 mm）、横版（90 × 24.4 mm）和图标（40 × 40 mm）三种，各有按实际尺寸排版的矢量 PDF（下单用，文字已转曲）、600 dpi 透明底 PNG 和 SVG。字体为 Montserrat（SIL Open Font License，`logo/fonts/`）。

```bash
python logo/make_logo.py     # SVG
python logo/export.py        # PDF / PNG（Edge 无头模式），尺寸在 SIZES 中修改
```
