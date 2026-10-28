"""Derive build_poster_zh.py from build_poster.py (Chinese texts and fonts). Usage: python make_zh.py <this directory>
import os, sys
D = sys.argv[1]
s = open(os.path.join(D, 'build_poster.py'), encoding='utf-8').read()


def blk(start, end, new):
    global s
    i0 = s.index(start); i1 = s.index(end, i0)
    s = s[:i0] + new + s[i1:]


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:60]
    s = s.replace(a, b)


rep('"""AMD poster template -> GlideLink poster (one 13.33 x 7.5 in slide).',
    '"""AMD poster template -> GlideLink poster, Chinese version (one 13.33 x 7.5 in slide; see build_poster.py).')
rep("INFO = json.load(open(os.path.join(HERE, 'team.json'), encoding='utf-8'))",
    "INFO = json.load(open(os.path.join(HERE, 'team_zh.json'), encoding='utf-8'))\nSERIF = ('Times New Roman', 'Cambria', 'Georgia', 'Garamond', 'Bookman Old Style', 'Century Schoolbook')\n"
    "# East Asian font follows the Latin one: serif -> 华文中宋, sans serif -> Noto Sans SC")
rep("t = set_paras(by_id[9], ['GlideLink: Real-Time DeepJSCC-Q', 'Image Transmission on AMD FPGA'])",
    "t = set_paras(by_id[9], ['GlideLink：基于AMD FPGA的', 'DeepJSCC-Q实时无线图像传输'])")
rep("by_id[8].top = Inches(1.1)                                       # INTRODUCTION",
    """by_id[8].top = Inches(1.1)
# section headings: one size, natural spacing; the black bars are sized to the text
HS, PAD = 22, 0.24                                   # heading size (pt), bar padding at each end (in)
BAR = Inches(4 * HS / 72 + 2 * PAD)
set_paras(by_id[8], ['作品简介'])
by_id[8].width = BAR
by_id[8].left = Inches(0.22 + 4.0 / 2) - BAR // 2               # centred over the left column (x 0.22 .. 4.22 in)
tf8 = by_id[8].text_frame
tf8.margin_left = tf8.margin_right = 0; tf8.vertical_anchor = MSO_ANCHOR.MIDDLE
tf8.margin_top = tf8.margin_bottom = 0
tf8._txBody.find(qn('a:bodyPr')).remove(tf8._txBody.find(qn('a:bodyPr')).find(qn('a:normAutofit')))
for p in tf8.paragraphs:
    p.alignment = PP_ALIGN.CENTER
    p.line_spacing = 1.0; p.space_before = Pt(0)
    for r in p.runs:
        r.font.size = Pt(HS)""")
rep("set_paras(by_id[19], ['G']); set_paras(by_id[20], ['N'])              # the template's vertical label reads DESING\n",
    """# vertical section labels: Chinese characters stacked on the template's black bars
for i in list(range(15, 29)) + list(range(33, 39)):
    remove(by_id[i])
for bar, text in ((by_id[10], '创新设计'), (by_id[32], '测试结果')):
    bar.width, bar.height = by_id[10].width, BAR
    vb = s.shapes.add_textbox(bar.left, bar.top + Inches(PAD), bar.width, Inches(4 * HS / 72))   # eaVert text starts at the top
    tf = vb.text_frame; tf.word_wrap = True; tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf._txBody.find(qn('a:bodyPr')).set('vert', 'eaVert')         # upright characters, top to bottom
    p = tf.paragraphs[0]; p.alignment = PP_ALIGN.CENTER
    r = p.add_run(); r.text = text
    r.font.size = Pt(HS); r.font.bold = True; r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
""")
rep("af.left, af.width = Inches(7.72), Inches(2.5)",
    "af.left, af.width = Inches(7.72), Inches(2.5)\nfor shp in (nm, af):\n    for p in shp.text_frame.paragraphs:\n        for r in p.runs:\n            r.font.size = Pt(15)\nnm.left, af.left = Inches(7.5), Inches(8.2)                      # closer to the badge")
rep("cap = set_paras(by_id[30], ['On-board test: RX (left), TX (right)', 'AMD Zynq UltraScale+ MPSoC'])",
    "cap = set_paras(by_id[30], ['板上实测：左为RX，右为TX', 'AMD Zynq UltraScale+ MPSoC'])")
blk("box(LX, 1.68, LW, 1.3, [[", "CH = [", """box(LX, 1.68, LW, 1.3, [[
    ('无人机、机器人和远程摄像头的实时视频要经过信噪比时刻变化的无线链路。', {}),
    ('分离信源信道编码（SSCC）', {'b': True}),
    ('先用JPEG压缩、再加信道编码：信噪比低于纠错门限时，比特错误沿变长码扩散，整幅图像无法解码（', {}),
    ('悬崖效应', {'b': True}), ('）；高于门限后画质也不再提升。', {})]])
box(LX, 3.0, LW, 0.22, [[('典型应用场景', {'b': True})]], size=12, align=PP_ALIGN.CENTER)
""")
rep("CH = [('Camera', 'drone / robot'), ('Encoder', 'FPGA, TX'), ('Radio channel', 'noise, fading'),\n      ('Decoder', 'FPGA, RX'), ('Display', 'live view')]",
    "CH = [('摄像头', '无人机等'), ('编码器', 'FPGA，发端'), ('无线信道', '噪声、衰落'),\n      ('解码器', 'FPGA，收端'), ('显示', '实时画面')]")
blk("box(LX, 3.98, LW, 3.15, [", "# ---------------------------------------------------------------- CREATIVE DESIGN", """box(LX, 3.98, LW, 3.15, [
    [('深度联合信源信道编码（DeepJSCC）', {'b': True}),
     ('用一对卷积神经网络把像素直接映射为信道符号，画质随信噪比平缓变化。', {}),
     ('DeepJSCC-Q', {'b': True}),
     ('（Tung等，JSAIT 2022）进一步把符号约束到QAM星座上，可接入标准数字物理层；但它计算量大，'
      '接收端还必须把未经判决的软符号送入解码器。', {})],
    [('GlideLink', {'b': True}),
     ('用两块AMD Zynq UltraScale+ MPSoC开发板（PYNQ-ZU）和AD9361射频子卡搭建了完整的实时链路：'
      'DeepJSCC-Q编码器、解码器和类802.11a OFDM物理层全部在PL中实现，', {}),
     ('经空口以30 fps传输256×256彩色图像', {'b': True}),
     ('；PS负责取图和射频配置，并在触摸屏上实时显示链路状态。', {})],
    [('关键技术问题：', {'b': True}),
     ('每帧1.15 GMAC的神经网络编解码器要以30 fps放进一片FPGA，且权重全部片上；683个符号的长OFDM帧要同步、'
      '跟踪到足以直接解码软符号的精度；还要在同一物理层上与JPEG + LDPC做公平的空口对比。', {})],
], space=7)

""")
for a, b in [("('TX board' if tx else 'RX board', {'b': True})", "('发端板' if tx else '收端板', {'b': True})"),
             ("'4× Cortex-A53, Linux: camera, radio setup'", "'4×A53，Linux：取图、射频配置'"),
             ("'CNN encoder, OFDM TX, LDPC encoder'", "'CNN编码、OFDM发射、LDPC编码'"),
             ("'USB camera'", "'摄像头'"),
             ("'A53 + Mali-400 GPU: GUI, web page'", "'A53 + Mali-400：界面、网页'"),
             ("'OFDM RX, CNN decoder, LDPC decoder'", "'OFDM接收、CNN解码、LDPC译码'"),
             ("'Touch screen'", "'触摸屏'"), ("'Phone'", "'手机'"), ("'RF card'", "'射频子卡'"),
             ("'over the air'", "'空口'"), ("'Ethernet: TX preview, control'", "'以太网：TX预览图、控制'"),
             ("[[('System Architecture', {'b': True})]]", "[[('系统组成', {'b': True})]]"),
             ("[[('Key innovations and technical issues addressed:', {'b': True})]]", "[[('关键创新点与解决的技术问题', {'b': True})]]"),
             ("'Over-the-air PSNR (DIV2K 0802, 32,768 64QAM symbols per image)'", "'空口实测PSNR（DIV2K 0802，每幅图32,768个64QAM符号）'"),
             ("'Over-the-air SNR (dB)'", "'空口SNR (dB)'"),
             ("set('vert', 'vert')", "set('vert', 'eaVert')"),
             ("ROWS = [('Design', 'LUT', 'BRAM36', 'DSP', 'WNS (ns)', 'Power*'),", "ROWS = [('工程', 'LUT', 'BRAM36', 'DSP', 'WNS (ns)', '功耗*'),"),
             ("'* measured, PS + PL + DDR; xczu5eg after routing'", "'* 功耗为实测值，含PS、PL与DDR；资源为xczu5eg布局布线后结果'")]:
    rep(a, b)
blk("box(MX, 3.9, 3.95, 2.2, [", "# ---------------------------------------------------------------- RESULT", """box(MX, 3.9, 3.95, 2.2, [
    [('1. 流式CNN加速器：', {'b': True}),
     ('W8A12整数运算，64个卷积引擎组成层流水，运行于250 MHz；权重与激活全部片上存储，不访问DDR。', {})],
    [('2. 非对称浅层解码器：', {'b': True}),
     ('与编码器联合训练，乘加量降为原解码器的1/102；卷积工作量是已发表FPGA DeepJSCC实现（GLOBECOM 2025）的5.2倍，'
      'DSP和BRAM用量却更少。', {})],
    [('3. 软符号OFDM物理层：', {'b': True}),
     ('683个符号的长帧、PN扰码、二阶SFO/CPE跟踪；均衡后的I/Q不经判决直接送入解码器。', {})],
], space=5)
box(MX, 5.98, 1.5, 1.2, [[('4. 完整演示系统：', {'b': True}),
     ('从摄像头到触摸屏，实时显示PSNR和星座图，并用Mali-400 GPU绘制信道三维图。', {})]])

""")
blk("box(9.25, 4.17, 4.0, 0.62, [[", "picture(os.path.join(HERE, 'strip.png')", """box(9.25, 4.17, 4.0, 0.62, [[
    ('DeepJSCC-Q在高SNR时比JPEG + LDPC高', {}), ('2.8 dB', {'b': True}), ('，SNR降到2.9 dB时仍有', {}),
    ('23.5 dB', {'b': True}), ('；基线在16～19.5 dB处断崖式失效。', {})]])
""")
blk("box(9.25, 6.38, 4.0, 0.2, [[('30 fps'", "ROWS = [", """box(9.25, 6.38, 4.0, 0.2, [[('30 fps', {'b': True}), ('连续运行10分钟（18,020帧），PL内端到端时延', {}),
                             ('67.5 ms', {'b': True})]], size=10)
""")
rep("prs.save(OUT)", """# East Asian font on every run of the slide and of the chart: 华文中宋 next to a serif Latin font, else Noto Sans SC;
# the section headings (Aharoni in the English version) get the Black weight
HEADINGS = {'作品简介', '创新设计', '测试结果'}
for sp in s.shapes:
    if sp.has_text_frame and sp.text_frame.text.replace('\\\\n', '').replace('\\\\v', '') in HEADINGS:
        for rpr in sp._element.iter(qn('a:rPr')):
            rpr.set('heading', '1')
for root in (s._element, gf.chart._chartSpace):
    for rpr in list(root.iter(qn('a:rPr'), qn('a:endParaRPr'), qn('a:defRPr'))):
        for e in rpr.findall(qn('a:ea')):
            rpr.remove(e)
        if rpr.tag == qn('a:rPr'):
            rpr.set('lang', 'zh-CN'); rpr.set('altLang', 'en-US')        # Chinese line breaking rules
        lat = rpr.find(qn('a:latin'))
        ea = etree.Element(qn('a:ea'))
        if rpr.attrib.pop('heading', None):
            ea.set('typeface', 'Noto Sans SC Black')
        else:
            ea.set('typeface', '华文中宋' if lat is not None and lat.get('typeface') in SERIF else 'Noto Sans SC')
        if lat is not None:
            lat.addnext(ea)
        else:
            nxt = [c for c in rpr if c.tag in (qn('a:cs'), qn('a:sym'), qn('a:hlinkClick'), qn('a:hlinkMouseOver'),
                                              qn('a:rtl'), qn('a:extLst'))]
            if nxt:
                nxt[0].addprevious(ea)
            else:
                rpr.append(ea)
prs.save(OUT)""")
open(os.path.join(D, 'build_poster_zh.py'), 'w', encoding='utf-8').write(s)
print('ok')
