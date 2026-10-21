"""Build the pandoc reference document from the competition template.

The template only defines Normal / heading 1 / heading 2. This script keeps its page setup, header (logo) and
footer (page number) and replaces the style sheet: body text in docDefaults (so that table cells can use a smaller
size from the table style), headings 1-4, figure / caption paragraphs and a bordered table style.

    python -I make_reference_docx.py <template.docx> <reference.docx>
"""
import re
import sys
import zipfile

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
CJK, LATIN, HEI = "宋体", "Times New Roman", "黑体"


def rfonts(east=CJK, latin=LATIN):
    return f'<w:rFonts w:ascii="{latin}" w:hAnsi="{latin}" w:eastAsia="{east}" w:cs="{latin}"/>'


def pstyle(sid, name, ppr, rpr, based="Normal", nxt=None, extra=""):
    return (f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/>'
            f'<w:basedOn w:val="{based}"/>' + (f'<w:next w:val="{nxt}"/>' if nxt else "") + extra +
            f'<w:qFormat/><w:pPr>{ppr}</w:pPr><w:rPr>{rpr}</w:rPr></w:style>')


def heading(level, size_half_pt, east, align="left", before=240, after=120, bold=True):
    jc = '<w:jc w:val="center"/>' if align == "center" else ""
    ppr = (f'<w:keepNext/><w:keepLines/><w:spacing w:before="{before}" w:after="{after}" w:line="240" '
           f'w:lineRule="auto"/><w:ind w:firstLine="0" w:firstLineChars="0"/>{jc}<w:outlineLvl w:val="{level - 1}"/>')
    rpr = rfonts(east) + ('<w:b/><w:bCs/>' if bold else '') + f'<w:sz w:val="{size_half_pt}"/><w:szCs w:val="{size_half_pt}"/>'
    return pstyle(f"Heading{level}", f"heading {level}", ppr, rpr, nxt="FirstParagraph")


def build_styles():
    body_ppr = '<w:spacing w:before="0" w:after="0" w:line="300" w:lineRule="auto"/><w:jc w:val="both"/>'
    defaults = (f'<w:docDefaults><w:rPrDefault><w:rPr>{rfonts()}<w:kern w:val="2"/><w:sz w:val="24"/>'
                f'<w:szCs w:val="24"/><w:lang w:val="en-US" w:eastAsia="zh-CN"/></w:rPr></w:rPrDefault>'
                f'<w:pPrDefault><w:pPr>{body_ppr}</w:pPr></w:pPrDefault></w:docDefaults>')
    indent = '<w:ind w:firstLineChars="200" w:firstLine="480"/>'
    styles = [
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/>'
        '<w:pPr><w:widowControl w:val="0"/></w:pPr></w:style>',
        '<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont">'
        '<w:name w:val="Default Paragraph Font"/><w:uiPriority w:val="1"/><w:semiHidden/></w:style>',
        heading(1, 32, HEI, "center", 360, 240),   # 三号
        heading(2, 28, HEI, before=240, after=120),  # 四号
        heading(3, 24, HEI, before=180, after=60),   # 小四
        heading(4, 24, HEI, before=120, after=0),
        heading(5, 24, HEI, before=60, after=0, bold=False),
        pstyle("BodyText", "Body Text", indent, ""),
        pstyle("FirstParagraph", "First Paragraph", indent, "", based="BodyText", nxt="BodyText"),
        pstyle("Compact", "Compact", '<w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>'
               '<w:ind w:firstLine="0" w:firstLineChars="0"/><w:jc w:val="left"/>', ""),
        pstyle("FigurePara", "FigurePara", '<w:keepNext/><w:spacing w:before="120" w:after="0"/>'
               '<w:ind w:firstLine="0" w:firstLineChars="0"/><w:jc w:val="center"/>', ""),
        pstyle("EquationPara", "EquationPara", '<w:spacing w:before="60" w:after="60"/>'
               '<w:ind w:firstLine="0" w:firstLineChars="0"/><w:jc w:val="center"/>', ""),
        pstyle("CaptionPara", "CaptionPara", '<w:spacing w:before="60" w:after="160" w:line="240" '
               'w:lineRule="auto"/><w:ind w:firstLine="0" w:firstLineChars="0"/><w:jc w:val="center"/>',
               '<w:sz w:val="21"/><w:szCs w:val="21"/>'),
        pstyle("TableCaption", "TableCaption", '<w:keepNext/><w:spacing w:before="160" w:after="60" w:line="240" '
               'w:lineRule="auto"/><w:ind w:firstLine="0" w:firstLineChars="0"/><w:jc w:val="center"/>',
               '<w:sz w:val="21"/><w:szCs w:val="21"/>'),
        # code / pseudocode: thin grey frame and light fill so that it stands apart from the body text
        pstyle("SourceCode", "Source Code", '<w:pBdr>' + ''.join(
                   f'<w:{e} w:val="single" w:sz="4" w:space="4" w:color="808080"/>' for e in ('top', 'left', 'bottom', 'right'))
               + '</w:pBdr><w:shd w:val="clear" w:color="auto" w:fill="F5F5F5"/>'
               '<w:spacing w:before="60" w:after="60" w:line="240" w:lineRule="auto"/>'
               '<w:ind w:firstLine="0" w:firstLineChars="0" w:left="420" w:right="420"/><w:jc w:val="left"/>',
               rfonts(CJK, "Consolas") + '<w:sz w:val="18"/><w:szCs w:val="18"/>'),
        # Greek letters in body text: Word would otherwise draw them with the East Asian font (SimSun)
        '<w:style w:type="character" w:styleId="Greek"><w:name w:val="Greek"/>'
        f'<w:rPr>{rfonts(LATIN, LATIN)}</w:rPr></w:style>',
        '<w:style w:type="character" w:styleId="VerbatimChar"><w:name w:val="Verbatim Char"/>'
        f'<w:rPr>{rfonts(CJK, "Consolas")}<w:sz w:val="21"/></w:rPr></w:style>',
        pstyle("header", "header", '<w:jc w:val="center"/>', '<w:sz w:val="18"/>'),
        pstyle("footer", "footer", '<w:jc w:val="center"/>', '<w:sz w:val="18"/>'),
        '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/>'
        '<w:uiPriority w:val="99"/><w:semiHidden/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar>'
        '<w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/>'
        '<w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>',
        '<w:style w:type="table" w:styleId="Table"><w:name w:val="Table"/><w:basedOn w:val="TableNormal"/>'
        '<w:pPr><w:spacing w:before="20" w:after="20" w:line="240" w:lineRule="auto"/><w:ind w:firstLine="0" '
        'w:firstLineChars="0"/><w:jc w:val="left"/></w:pPr><w:rPr><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr>'
        '<w:tblPr><w:jc w:val="center"/><w:tblBorders>'
        '<w:top w:val="single" w:sz="8" w:space="0" w:color="000000"/>'
        '<w:left w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
        '<w:bottom w:val="single" w:sz="8" w:space="0" w:color="000000"/>'
        '<w:right w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
        '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
        '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="000000"/></w:tblBorders>'
        '<w:tblCellMar><w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr>'
        '<w:tblStylePr w:type="firstRow"><w:rPr><w:b/><w:bCs/></w:rPr>'
        '<w:tcPr><w:shd w:val="clear" w:color="auto" w:fill="EDEDED"/></w:tcPr></w:tblStylePr></w:style>',
    ]
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:styles {W}>' + defaults +
            "".join(styles) + "</w:styles>")


def main(src, dst):
    zin = zipfile.ZipFile(src)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/styles.xml":
                data = build_styles().encode("utf-8")
            elif item.filename in ("word/header1.xml", "word/footer1.xml"):
                # the header / footer paragraphs referenced the template's style ids "6" / "5"
                data = re.sub(rb'<w:pStyle w:val="6"/>', b'<w:pStyle w:val="header"/>', data)
                data = re.sub(rb'<w:pStyle w:val="5"/>', b'<w:pStyle w:val="footer"/>', data)
            zout.writestr(item, data)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
