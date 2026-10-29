"""Add a cover page and a table of contents in front of the laid-out design_report.docx (Word COM).

Section 1 = cover (no header, no page number) + contents (roman page numbers); section 2 = the report, page
numbers restart at 1, so the body keeps its page numbers and layout.

    python cover_toc.py <in.docx> <out.docx> <cover photo> <competition logo>
"""
import os, shutil, sys
import win32com.client

src, out, photo, logo = (os.path.abspath(a) for a in sys.argv[1:5])
shutil.copy(src, out)
HEI, SONG, TNR = '黑体', '宋体', 'Times New Roman'

w = win32com.client.DispatchEx('Word.Application'); w.Visible = False; w.DisplayAlerts = 0
try:
    d = w.Documents.Open(out, AddToRecentFiles=False)
    d.Range(0, 0).InsertBreak(2)                         # wdSectionBreakNextPage: new section 1 in front
    s1, s2 = d.Sections(1), d.Sections(2)
    for hf in (s2.Headers(1), s2.Footers(1)):
        hf.LinkToPrevious = False                        # body keeps its own header / footer
    s2.Footers(1).PageNumbers.RestartNumberingAtSection = True
    s2.Footers(1).PageNumbers.StartingNumber = 1
    s1.PageSetup.DifferentFirstPageHeaderFooter = True   # cover page: empty header and footer
    s1.Footers(1).PageNumbers.NumberStyle = 1            # contents pages: I, II, ...

    def para(text, size, font=SONG, bold=False, before=0, after=0, align=1, latin=TNR):
        r = d.Range(s1.Range.End - 1, s1.Range.End - 1)  # just before the section break
        r.InsertBefore(text)
        p = r.Paragraphs(1)
        p.Style = d.Styles(-1)                           # wdStyleNormal
        p.Range.Font.NameFarEast = font; p.Range.Font.Name = latin
        p.Range.Font.Size = size; p.Range.Font.Bold = bold
        p.Format.Alignment = align; p.Format.FirstLineIndent = 0; p.Format.LeftIndent = 0
        p.Format.SpaceBefore = before; p.Format.SpaceAfter = after
        p.Format.LineSpacingRule = 0                      # single
        r.InsertParagraphAfter()
        return p

    first = s1.Range.Paragraphs(1)                        # the empty paragraph holding the section break
    para('[[LOGO]]', 12, before=0)
    para('全国大学生嵌入式芯片与系统设计竞赛', 16, HEI, before=20)
    para('2026  FPGA赛道  ·  自主选题（高级组）', 14, SONG, before=6)
    para('基于FPGA的DeepJSCC-Q', 26, HEI, True, before=40)
    para('实时无线图像传输系统', 26, HEI, True, before=6)
    para('GlideLink: Real-Time DeepJSCC-Q Image Transmission on AMD FPGA', 14, SONG, before=14, latin=TNR)
    para('[[PHOTO]]', 12, before=24)
    para('设计报告', 22, HEI, True, before=24)
    para('2026年10月', 15, SONG, before=20)
    f = d.Content; f.Find.Execute(FindText='[[PHOTO]]', MatchCase=True, Forward=True, Wrap=0)
    f.Text = ''
    shp = d.InlineShapes.AddPicture(photo, False, True, f)
    shp.LockAspectRatio = True; shp.Width = 290
    f = d.Content; f.Find.Execute(FindText='[[LOGO]]', MatchCase=True, Forward=True, Wrap=0)
    f.Text = ''
    shp = d.InlineShapes.AddPicture(logo, False, True, f)
    shp.LockAspectRatio = True; shp.Width = 240
    # contents page
    r = d.Range(s1.Range.End - 1, s1.Range.End - 1); r.InsertBreak(7)   # wdPageBreak
    tp = para('目　　录', 16, HEI, True, before=12, after=18)
    tocp = para('', 12)
    # TOC over the report body only (from 摘要 on), headings 1-3
    f = d.Content; f.Find.Execute(FindText='摘要', MatchCase=True, Forward=True, Wrap=0)
    start = f.Paragraphs(1).Range.Start
    d.Bookmarks.Add('ReportBody', d.Range(start, d.Content.End))
    for lvl, size, bold, font in ((-20, 12, True, HEI), (-21, 12, False, SONG), (-22, 10.5, False, SONG)):
        st = d.Styles(lvl)
        st.Font.NameFarEast = font; st.Font.Name = TNR; st.Font.Size = size; st.Font.Bold = bold
        st.ParagraphFormat.SpaceBefore = 6 if lvl == -20 else 0; st.ParagraphFormat.SpaceAfter = 0
        st.ParagraphFormat.LineSpacingRule = 0; st.ParagraphFormat.FirstLineIndent = 0
    rr = tocp.Range; rr.Collapse(1)
    d.Fields.Add(rr, -1, 'TOC \\o "1-3" \\h \\z \\b ReportBody', False)
    d.TablesOfContents(1).Update()
    # remove the empty paragraph that held the break (first paragraph of section 1) if still empty
    if first.Range.Text.strip() == '':
        pass
    d.Save(); d.Close(0)
finally:
    w.Quit()
print('ok')
