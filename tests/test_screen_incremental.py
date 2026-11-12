import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QLabel
import pytest
from test_screen import application, reader
from test_credentials_ui import controller
from linguafloat.hotkey_edit import HotkeyEdit
from linguafloat.hotkeys import parse_hotkey, HotkeyError
from linguafloat.screen_layout import translation_chunks, readable_lines


def test_incremental_out_of_order_error_and_rate_limit(reader,monkeypatch):
    now=[100.0];monkeypatch.setattr("linguafloat.screen.time.monotonic",lambda:now[0])
    requests=[];reader.translation_requested.connect(lambda text,t:requests.append((text,t)))
    reader._observe("First.\nSecond.\nThird.")
    assert [x[0] for x in requests]==["First.","Second."]
    reader.translated(requests[1][1],"Second.","第二段。")
    assert "第二段。" in reader.panel.translation.toPlainText()
    assert "翻译完成" != reader.panel.status.text()
    reader.translated(requests[0][1],"First.","第一段。")
    assert reader.panel.translation.toPlainText().index("第一段") < reader.panel.translation.toPlainText().index("第二段")
    assert len(requests)==2
    now[0]+=3;reader._pump();assert len(requests)==3
    reader.translated(requests[2][1],"Third.","HTTP 429",error=True)
    assert "部分完成" in reader.panel.status.text() and reader.state.running
    now[0]+=30;reader._pump();reader._observe("First.\nSecond.\nThird.")
    assert len(requests)==3


def test_paused_old_results_and_cancelled_slots(reader,monkeypatch):
    now=[100.];monkeypatch.setattr("linguafloat.screen.time.monotonic",lambda:now[0])
    requests=[];reader.translation_requested.connect(lambda text,t:requests.append((text,t)))
    reader._observe("Old one.\nOld two.")
    reader.pause();before=reader.panel.translation.toPlainText()
    reader.translated(requests[0][1],requests[0][0],"stale")
    assert reader.panel.translation.toPlainText()==before
    reader.cancel_request(requests[1][1]);assert not reader._block_jobs
    reader.resume();reader.settle_timer.stop();now[0]+=4
    reader._observe("New.")
    assert requests[-1][0]=="New."


def test_fast_first_capture_is_independent_of_refresh(reader,application,monkeypatch):
    calls=[];monkeypatch.setattr(reader,"tick",lambda:calls.append(True))
    reader.set_intervals(3.5,3)
    reader.resume()
    assert reader.settle_timer.interval()==1000 and not reader.timer.isActive()
    QTest.qWait(1150)
    assert calls==[True] and reader.timer.interval()==3500
    reader.pause();QTest.qWait(50);assert not reader.settle_timer.isActive()


def test_hotkey_record_and_cancel(application):
    edit=HotkeyEdit("Ctrl+Alt+T");edit.show();events=[]
    edit.recording_changed.connect(events.append)
    QTest.mouseClick(edit,Qt.MouseButton.LeftButton)
    assert edit.recording and edit.isReadOnly()
    QTest.keyClick(edit,Qt.Key.Key_Up,Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.ShiftModifier)
    assert edit.text()=="Ctrl+Shift+Up" and not edit.recording
    QTest.mouseClick(edit,Qt.MouseButton.LeftButton)
    QTest.keyClick(edit,Qt.Key.Key_Control)
    assert edit.recording
    QTest.keyClick(edit,Qt.Key.Key_Escape)
    assert edit.text()=="Ctrl+Shift+Up" and events==[True,False,True,False]
    edit.close()


@pytest.mark.parametrize("key",["Ctrl+Left","Alt+PageUp","Shift+F24","Ctrl+Space"])
def test_named_shortcuts(key):
    assert parse_hotkey(key).display==key


def test_settings_sync_and_save_failure(controller,monkeypatch):
    c,_,store=controller
    panel=c.screen_reader.panel
    panel.ocr_interval_spin.setValue(2.5)
    assert store.load().ocr_interval==2.5 and c.window.ocr_interval_spin.value()==2.5
    monkeypatch.setattr(store,"save",lambda s:(_ for _ in ()).throw(OSError("disk")))
    panel.ocr_interval_spin.setValue(4)
    assert panel.ocr_interval_spin.value()==2.5 and c.settings.ocr_interval==2.5


def test_removed_local_controls(controller):
    c,_,_=controller
    texts=[x.text() for x in c.window.dashboard.findChildren(QPushButton)]
    assert not any("从本机" in x or "选择文件" in x for x in texts)
    assert "选择 SQL 文件" in texts and "导出 SQL 备份" in texts


def test_shortcuts_conflict_and_disk_rollback(controller,monkeypatch):
    c,_,store=controller
    monkeypatch.setattr(c.input_hotkey,"start",lambda *a:None)
    old=c.settings.input_hotkey
    c._save_input_hotkey(c.settings.hotkey)
    assert c.settings.input_hotkey==old and c.window.input_hotkey_edit.text()==old
    monkeypatch.setattr(store,"save",lambda s:(_ for _ in ()).throw(OSError("disk")))
    c._save_input_hotkey("Ctrl+Shift+F9")
    assert c.settings.input_hotkey==old and c.window.input_hotkey_edit.text()==old


def test_layout_preserves_content_and_no_single_letter_tail():
    text="The final complete package has been updated. Native window checks at 125% and 150% scaling have passed."
    lines=readable_lines(text,38,len)
    assert "".join("".join(lines).split())=="".join(text.split())
    assert all(len(line)>1 for line in lines)
    assert readable_lines("One short sentence.",40,len)==["One short sentence."]
    assert readable_lines("superlongwordwithoutbreak",8,len)==["superlongwordwithoutbreak"]
    assert translation_chunks("This is a wrapped\nEnglish sentence.\nNext sentence.")==["This is a wrapped English sentence.","Next sentence."]


def test_outline_wide_move_area_and_eight_resize_handles(reader):
    outline=reader.outline;outline.show_region(reader.region)
    w,h=outline.width(),outline.height()
    for point,expected in ((QPoint(1,1),"topleft"),(QPoint(w-1,1),"topright"),
        (QPoint(1,h-1),"bottomleft"),(QPoint(w-1,h-1),"bottomright"),
        (QPoint(w//2,1),"top"),(QPoint(w//2,h-1),"bottom"),
        (QPoint(1,h//2),"left"),(QPoint(w-1,h//2),"right")):
        assert outline._corner_at(point)==expected
    assert outline._corner_at(QPoint(w//4,2))=="top"
    assert outline._corner_at(outline.rect().center())==""
    assert outline.mask().contains(outline.rect().center())


def test_ocr_bounds_stay_attached_to_reading_order(reader):
    reader.frame_regions=[{"text":"One.","box":(10,20,100,40)},
                          {"text":"Two.","box":(10,50,100,70)}]
    reader._observe("One.\nTwo.")
    assert reader._batch["regions"]==[[(10,20,100,40)],[(10,50,100,70)]]


def test_cached_synchronous_results_are_not_replaced_by_pending(reader):
    reader.translation_requested.connect(lambda source,token:reader.translated(token,source,"缓存译文"))
    reader._observe("One.\nTwo.")
    assert all(b[2]=="done" for b in reader._batch["blocks"])
    assert "等待翻译" not in reader.panel.translation.toPlainText()
    assert not reader._block_jobs and reader.state.pending is None


def test_selection_hotkey_failure_restores_form_and_settings(controller,monkeypatch):
    c,_,store=controller
    old=c.settings.hotkey
    def fail(*args):raise HotkeyError("Windows 注册失败（错误 1409）：已占用")
    monkeypatch.setattr(c,"_configure_triggers",fail)
    c.window.hotkey_edit.setText("Ctrl+Shift+F8")
    c._save_function_settings()
    assert c.settings.hotkey==old and store.load().hotkey==old
    assert c.window.hotkey_edit.text()==old


def test_actual_hotkey_listener_is_released_while_recording(controller,monkeypatch):
    c,_,_=controller;events=[]
    for binding in (c.hotkey,c.input_hotkey):
        monkeypatch.setattr(binding,"stop",lambda:events.append("stop"))
        monkeypatch.setattr(binding,"start",lambda *args:events.append("start"))
    c.window.hotkey_edit.begin_recording()
    assert c._hotkey_recording and events==["stop","stop"]
    c.window.hotkey_edit.end_recording()
    assert not c._hotkey_recording and events.count("start")==2
