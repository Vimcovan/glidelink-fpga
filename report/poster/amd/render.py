import sys, os, win32com.client
src=os.path.abspath(sys.argv[1]); out=os.path.abspath(sys.argv[2]); w=int(sys.argv[3]) if len(sys.argv)>3 else 2400
app=win32com.client.DispatchEx("PowerPoint.Application")
try:
    p=app.Presentations.Open(src, True, False, False)
    h=round(w*p.PageSetup.SlideHeight/p.PageSetup.SlideWidth)
    p.Slides(1).Export(out, "PNG", w, h)
    if len(sys.argv)>4: p.SaveAs(os.path.abspath(sys.argv[4]), 32)   # pdf
    p.Close()
finally:
    if app.Presentations.Count==0: app.Quit()
