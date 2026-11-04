"""Local widget checks at two sizes and two Qt scaling factors; no API."""
import json,os
from pathlib import Path
from PySide6.QtCore import QPoint,QRect
from PySide6.QtWidgets import QApplication
from linguafloat.config import AppSettings
from linguafloat.ui import MainWindow,apply_ui_font
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/pdf-graphics-v0.9.1'
app=QApplication([]);apply_ui_font(app,14)
window=MainWindow(AppSettings(),'missing')
page=window.documents
page.restore_preferences(AppSettings())
page.translate_graphics.setChecked(True)
source=next(Path('C:/Users/Astatine/Zotero/storage/2U94QDNZ').glob('*.pdf'))
page.add_files([str(source)])
scale=os.environ.get('QT_SCALE_FACTOR','1')
results=[]
try:
 for width,height in ((860,620),(1060,840)):
  window.resize(width,height);window.show();window.pages.setCurrentIndex(2);app.processEvents()
  page.scroll.verticalScrollBar().setValue(0);app.processEvents()
  assert page.scroll.horizontalScrollBar().maximum()==0
  for widget in (page.start_button,page.pause_button,page.cancel_button):
   rect=QRect(widget.mapTo(window,QPoint()),widget.size())
   assert window.rect().contains(rect)
   assert widget.width()>=widget.minimumSizeHint().width()
  window.grab().save(str(OUT/f'ui-{width}x{height}-{scale}-top.png'))
  page.scroll.ensureWidgetVisible(page.translate_graphics);app.processEvents()
  assert page.translate_graphics.width()>=page.translate_graphics.minimumSizeHint().width()
  window.grab().save(str(OUT/f'ui-{width}x{height}-{scale}-settings.png'))
  window.pages.setCurrentIndex(1);app.processEvents()
  window.config_scroll.ensureWidgetVisible(window.document_timeout_spin);app.processEvents()
  assert window.config_scroll.horizontalScrollBar().maximum()==0
  assert window.document_timeout_spin.isVisible() and window.retries_spin.isVisible()
  window.grab().save(str(OUT/f'ui-{width}x{height}-{scale}-provider.png'))
  results.append(dict(size=[width,height],scale=scale,queue_columns=page.files.columnCount(),history_columns=page.history.columnCount(),horizontal_scroll=False))
 (OUT/f'ui-verification-{scale}.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(results,ensure_ascii=False))
finally:
 window.allow_close();window.close()
