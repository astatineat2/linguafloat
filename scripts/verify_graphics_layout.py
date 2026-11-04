"""Real-page layout evidence with explicitly manual translations, no API calls."""
from pathlib import Path
from types import SimpleNamespace
import json
import pymupdf as fitz
from linguafloat.document_core import DocumentJob, JobControl, JobStore
from linguafloat.document_graphics import DocumentEnhancer
from linguafloat.document_pdf import append_bilingual

ROOT=Path(__file__).resolve().parents[1]
SOURCE=next(Path('C:/Users/Astatine/Zotero/storage/2U94QDNZ').glob('*.pdf'))
PRIOR=next((ROOT.parent/'test').glob('试译版_Liyi*.ea988cf96810/*.双语.pdf'))
OUT=ROOT/'artifacts'/'pdf-graphics-v0.9.1'
TRANSLATIONS={
 'Department of Electrical Engineering, Harbin Institute of Technology, Harbin 150001, China':'哈尔滨工业大学电气工程系，中国哈尔滨 150001',
 'Research Institute of Functional Materials, Central Iron and Steel Research Institute, Beijing 100081, China':'钢铁研究总院功能材料研究所，中国北京 100081',
 '0018-9464 © 2014 IEEE. Personal use is permitted, but republication/redistribution requires IEEE permission.':'0018-9464 © 2014 IEEE。允许个人使用；再版或再分发须获得 IEEE 许可。',
 'Symbol':'符号','Quantity':'物理量','Value':'数值','outer diameter':'外径','inner diameter':'内径','height':'高度',
 'turns of primary winding':'一次绕组匝数','turns of secondary winding':'二次绕组匝数',
 'normal permeability':'常规磁导率','first tested permeability':'首次测试磁导率','second tested permeability':'第二次测试磁导率',
 'magentic field flux density':'磁通密度','magnetic field flux density':'磁通密度','magnetic field strength':'磁场强度',
}

def main():
 OUT.mkdir(parents=True,exist_ok=True)
 job=DocumentJob(str(SOURCE),str(OUT),{},pdf_mode='layout',translate_tables=True,translate_figures=True)
 store=JobStore(OUT/'offline-cache.sqlite')
 def translate(text,*args,**kwargs):
  if text not in TRANSLATIONS:raise ValueError('Manual fixture missing: '+text)
  return TRANSLATIONS[text]
 translator=SimpleNamespace(job=job,store=store,control=JobControl(),translate_fragment=translate)
 enhancer=DocumentEnhancer(translator,lambda e: print(e.get('message',''),flush=True) if e.get('message') else None)
 report={'validation':'manual translations; no API; only original page 2; body reused from user real output','warnings':[]}
 try:
  with fitz.open(SOURCE) as source,fitz.open(PRIOR) as prior,fitz.open() as output:
   for number in (1,):
    with fitz.open() as fragment:
     p=fragment.new_page(width=612,height=792)
     p.show_pdf_page(p.rect,prior,number,clip=fitz.Rect(632,26,1244,818))
     # Match final assembly: source graphics replace earlier shifted engine content.
     for area in enhancer.analyzer.discover(source[number],enhancer.digest):
      rect=fitz.Rect(area['box'])
      p.add_redact_annot(rect,fill=False,cross_out=False)
      p.apply_redactions(images=0,graphics=0,text=0)
      p.draw_rect(rect,color=None,fill=(1,1,1),overlay=True)
      p.show_pdf_page(rect,source,number,clip=rect)
     enhancer.apply(source[number],p)
     append_bilingual(output,source,number,fragment,'side_by_side')
     p.get_pixmap(matrix=fitz.Matrix(1.6,1.6)).save(OUT/f'after-{number+1}.png')
     prior[number].get_pixmap(matrix=fitz.Matrix(1.6,1.6),clip=fitz.Rect(632,26,1244,818)).save(OUT/f'before-{number+1}.png')
   output.save(OUT/'本地排版验证.pdf',garbage=4,deflate=True)
  enhancer.report(report)
  (OUT/'layout-evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps(report,ensure_ascii=False),flush=True)
 finally:store.close()

if __name__=='__main__':main()
