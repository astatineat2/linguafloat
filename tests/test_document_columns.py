import json
from PySide6.QtCore import QPoint,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QHeaderView
from test_credentials_ui import application,controller
from linguafloat.config import ConfigStore,AppSettings
from linguafloat.document_ui import DocumentPage


def test_drag_fit_restore_and_updates_keep_columns(controller,tmp_path):
    c,_,store=controller
    page=c.window.documents
    c.window.resize(860,620);c.window.pages.setCurrentIndex(2);c.window.show()
    source=tmp_path/('a_very_long_research_filename_'*5+'.pdf');source.write_bytes(b'pdf')
    page.add_files([str(source)])
    rowid=next(iter(page.rows))
    for name,index in [('files',0),('history',1)]:
        tree=getattr(page,name);page.file_tabs.setCurrentIndex(index)
        page.scroll.verticalScrollBar().setValue(0)
        QTest.qWait(30)
        header=tree.header()
        assert all(header.sectionResizeMode(i)==QHeaderView.ResizeMode.Interactive for i in range(tree.columnCount()))
        old=tree.columnWidth(0)
        point=QPoint(header.sectionViewportPosition(0)+old-1,header.height()//2)
        QTest.mousePress(header.viewport(),Qt.MouseButton.LeftButton,pos=point)
        QTest.mouseMove(header.viewport(),point+QPoint(75,0),delay=20)
        QTest.mouseRelease(header.viewport(),Qt.MouseButton.LeftButton,pos=point+QPoint(75,0))
        assert tree.columnWidth(0)>old+40
        assert tree.horizontalScrollBar().maximum()>0
    page.file_tabs.setCurrentIndex(0)
    header=page.files.header();width=page.files.columnWidth(0)
    point=QPoint(width-1,header.height()//2)
    QTest.mouseDClick(header.viewport(),Qt.MouseButton.LeftButton,pos=point)
    QTest.qWait(30)
    assert page.files.columnWidth(0)>width  # Full long name on double click.
    assert page.files.header().sectionResizeMode(0)==QHeaderView.ResizeMode.Interactive
    page.files.setColumnWidth(0,430);page.history.setColumnWidth(0,390)
    widths=page.preferences()['document_column_widths']
    page.update_row(rowid,'翻译中',45)
    page.set_history([('id',str(source),'completed',1,json.dumps({'file_size':4400000,'elapsed_seconds':655,
                           'input_tokens':474162,'output_tokens':25984,'untranslated_regions':10}))])
    c.window.resize(1060,840);QTest.qWait(220)
    assert page.preferences()['document_column_widths']==widths
    loaded=store.load()
    assert loaded.document_column_widths==widths
    restored=DocumentPage();restored.restore_preferences(loaded)
    assert restored.preferences()['document_column_widths']==widths
    item=page.history.topLevelItem(0)
    assert item.text(2)=='4.2 MB' and item.text(3)=='10分55秒'
    assert '10 处保留原文' in item.toolTip(6)
    page.file_tabs.setCurrentIndex(1);page.reset_columns.click();QTest.qWait(220)
    assert page.files.columnWidth(0)==430
    assert page.history.columnWidth(0)==page._default_columns(page.history,'history')[0]
    assert store.load().document_column_widths['history'][0]==page.history.columnWidth(0)
    restored.close()


def test_column_settings_reject_invalid_data(tmp_path):
    path=tmp_path/'settings.json'
    path.write_text(json.dumps({'document_column_widths':{'files':[300,True,120,300],'history':[300,80]}}))
    assert ConfigStore(path).load().document_column_widths=={}


def test_defaults_fit_headers_and_numbers(application):
    page=DocumentPage()
    for name in ('files','history'):
        tree=getattr(page,name)
        for column in range(tree.columnCount()):
            assert tree.columnWidth(column)>=tree.fontMetrics().horizontalAdvance(tree.headerItem().text(column))+24
    # Empty queue's custom progress column can still be auto-sized.
    page.fit_column(page.files,3)
    page.close()
