"""Keep table rows/code lead-ins together and size the dense resource table.

This postprocess preserves equations, images, text and unrelated formatting.
Usage: python fix_docx_layout.py design_report.docx
"""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import sys
import re
from lxml import etree as E

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
NS={'w':W}

def tag(name): return '{'+W+'}'+name
def child(parent,name):
    found=parent.find(tag(name))
    if found is None: found=E.SubElement(parent,tag(name))
    return found
def prop(parent,name,**attrs):
    node=child(parent,name)
    for k,v in attrs.items(): node.set(tag(k),str(v))
    return node
def keep_next(para):
    # w:keepNext sits right after w:pStyle in the schema order of w:pPr
    pp=para.find(tag('pPr'))
    if pp is None: pp=E.Element(tag('pPr'));para.insert(0,pp)
    if pp.find(tag('keepNext')) is None:
        style=pp.find(tag('pStyle'))
        pp.insert(0 if style is None else 1,E.Element(tag('keepNext')))
def text(node): return ''.join(node.xpath('.//w:t/text()',namespaces=NS))

path=Path(sys.argv[1])
with ZipFile(path) as z: parts={n:z.read(n) for n in z.namelist()}
root=E.fromstring(parts['word/document.xml'])
row_count=0
resource_count=0
for table in root.xpath('//w:tbl',namespaces=NS):
    rows=table.findall(tag('tr'))
    for k,row in enumerate(rows):
        trpr=row.find(tag('trPr'))
        if trpr is None:
            trpr=E.Element(tag('trPr')); row.insert(0,trpr)
        prop(trpr,'cantSplit',val='1')
        if k==0: prop(trpr,'tblHeader',val='1')   # repeated if a table is still taller than a page
        if k<len(rows)-1:                          # keep the whole table on one page
            for para in row.xpath('.//w:p',namespaces=NS): keep_next(para)
        row_count+=1
    first=table.find(tag('tr'))
    headers=[text(cell) for cell in first.findall(tag('tc'))] if first is not None else []
    if headers[:6]==['工程','LUT','FF','BRAM36','URAM','DSP']:
        widths=[650,1100,1100,1040,1040,1040,1100,1236]
        assert len(headers)==len(widths)
        pr=child(table,'tblPr')
        prop(pr,'tblLayout',type='fixed')
        prop(pr,'tblW',type='dxa',w=sum(widths))
        grid=child(table,'tblGrid')
        for node in list(grid): grid.remove(node)
        for width in widths: E.SubElement(grid,tag('gridCol')).set(tag('w'),str(width))
        for row in table.findall(tag('tr')):
            for cell,width in zip(row.findall(tag('tc')),widths):
                cp=child(cell,'tcPr')
                prop(cp,'tcW',type='dxa',w=width)
                margins=child(cp,'tcMar')
                for side in ['left','right']: prop(margins,side,type='dxa',w=54)
                for run in cell.xpath('.//w:r',namespaces=NS):
                    rp=run.find(tag('rPr'))
                    if rp is None: rp=E.Element(tag('rPr'));run.insert(0,rp)
                    prop(rp,'sz',val=19)
                    prop(rp,'szCs',val=19)
                for value in cell.xpath('.//w:t',namespaces=NS):
                    if value.text:
                        value.text=re.sub(r'(?<=\d) W\b','\u00a0W',value.text.replace('\u00a0W',' W'))
        resource_count+=1

body=root.find(tag('body'))
code_count=0
for previous,current in zip(list(body),list(body)[1:]):
    if previous.tag!=tag('p') or current.tag!=tag('p'): continue
    style=current.find('w:pPr/w:pStyle',NS)
    if style is not None and style.get(tag('val'))=='SourceCode' and text(previous).rstrip().endswith(('：',':')):
        keep_next(previous);code_count+=1

parts['word/document.xml']=E.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
with ZipFile(path,'w',ZIP_DEFLATED) as z:
    for name,blob in parts.items(): z.writestr(name,blob)
print(f'Layout: {row_count} unsplit rows, {resource_count} resource table, {code_count} code lead-ins')
