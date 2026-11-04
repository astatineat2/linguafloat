"""Offline UI render/interaction verification; no user's settings changed."""
import json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from PySide6.QtCore import QPoint,Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from linguafloat.config import AppSettings
from linguafloat.ui import MainWindow,apply_ui_font
OUT=ROOT/'artifacts/hobson-v0.9.2'
app=QApplication([])
# The offscreen plugin has no Windows font discovery; register the same system
# fonts used by the real Windows platform plugin before measuring/rendering.
for name in ('msyh.ttc','msyhbd.ttc','msyhl.ttc','seguiemj.ttf','segoeui.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
apply_ui_font(app,14)
window=MainWindow(AppSettings(),'missing');page=window.documents
source=next(Path('C:/Users/Astatine/Zotero/storage/3U8UUC6T').glob('*.pdf'))
page.add_files([str(source)])
page.set_history([('example',str(source),'completed',1,json.dumps(dict(file_size=source.stat().st_size,
    elapsed_seconds=655.804,input_tokens=474162,output_tokens=25984,untranslated_regions=10)))])
scale=os.environ.get('QT_SCALE_FACTOR','1');results=[]
try:
    for width,height in ((860,620),(1060,840)):
        window.resize(width,height);window.pages.setCurrentIndex(2);window.show();QTest.qWait(60)
        page.scroll.verticalScrollBar().setValue(0)
        for name,index in [('files',0),('history',1)]:
            page.file_tabs.setCurrentIndex(index);QTest.qWait(30)
            tree=getattr(page,name);tree.horizontalScrollBar().setValue(0)
            header=tree.header();old=tree.columnWidth(0)
            delta=max(40,tree.viewport().width()-sum(tree.columnWidth(i) for i in range(tree.columnCount()))+60)
            point=QPoint(old-1,header.height()//2)
            QTest.mousePress(header.viewport(),Qt.LeftButton,pos=point)
            QTest.mouseMove(header.viewport(),point+QPoint(delta,0),delay=20)
            QTest.mouseRelease(header.viewport(),Qt.LeftButton,pos=point+QPoint(delta,0))
            app.processEvents()
            assert tree.columnWidth(0)>old+20
            assert tree.horizontalScrollBar().maximum()>0
            window.grab().save(str(OUT/f'ui-{width}x{height}-{scale}-{name}.png'))
            tree.horizontalScrollBar().setValue(tree.horizontalScrollBar().maximum());app.processEvents()
            window.grab().save(str(OUT/f'ui-{width}x{height}-{scale}-{name}-right.png'))
            assert tree.columnWidth(2)>=tree.fontMetrics().horizontalAdvance('1000.0 MB')
            results.append(dict(size=[width,height],scale=scale,list=name,drag=True,horizontal_scroll=True))
            page.reset_columns.click()
    (OUT/f'ui-verification-{scale}.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results))
finally:
    window.allow_close();window.close()
