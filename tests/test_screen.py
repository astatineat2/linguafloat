import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import pytest

from linguafloat.screen import ScreenReader, ScreenTextState
from linguafloat.screen_ui import RegionOutline, RegionSelector, ScreenTranslationPanel, constrain_panel, panel_geometry
from test_app_controller import make_controller


@pytest.fixture
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def reader(application):
    class Window:
        def bring_to_front(self):
            pass
    instance = ScreenReader(Window(), lambda: "test-scope")
    instance.active_scope = "test-scope"
    instance.region = QRect(30, 30, 100, 60)
    instance.state.reset(True)
    yield instance
    instance.shutdown()
    instance.panel.deleteLater()
    instance.outline.deleteLater()


def stable(state, text):
    state.observe(text)
    state.observe(text)


def test_waits_for_stable_text_and_throttles():
    state = ScreenTextState(running=True)
    state.observe("Hello")
    assert state.next_request(0) is None
    state.observe("Hello")
    token, source = state.next_request(0)
    assert source == "Hello"
    assert state.finish(token)
    stable(state, "Next")
    assert state.next_request(2.9) is None
    assert state.next_request(3)[1] == "Next"


def test_pending_request_keeps_only_latest_stable_text():
    state = ScreenTextState(running=True)
    stable(state, "First")
    token, _ = state.next_request(0)
    for text in ("Second", "Third", "Latest"):
        stable(state, text)
        assert state.next_request(10) is None
    assert not state.finish(token)
    assert state.next_request(10)[1] == "Latest"


def test_pause_and_reselect_discard_old_result_without_parallel_requests():
    state = ScreenTextState(running=True)
    stable(state, "Old")
    token, _ = state.next_request(0)
    state.reset(False)
    assert state.next_request(10) is None
    state.reset(True)
    stable(state, "New")
    assert state.next_request(10) is None
    assert not state.finish(token)
    assert state.next_request(10)[1] == "New"


def test_identical_text_and_blank_do_not_resubmit():
    state = ScreenTextState(running=True)
    stable(state, "Same")
    token, _ = state.next_request(0)
    assert state.finish(token)
    for now in range(4, 12):
        state.observe("Same")
        assert state.next_request(now) is None
    stable(state, "")
    assert state.next_request(20) is None


@pytest.mark.parametrize("region,area", [
    (QRect(100, 100, 500, 250), QRect(0, 0, 1920, 1040)),
    (QRect(1500, 100, 400, 250), QRect(0, 0, 1920, 1040)),
    (QRect(0, 0, 1920, 150), QRect(0, 0, 1920, 1040)),
    (QRect(0, 800, 1920, 200), QRect(0, 0, 1920, 1040)),
    (QRect(-1800, 200, 500, 300), QRect(-1920, 0, 1920, 1040)),
])
def test_panel_never_overlaps_source_and_stays_on_screen(region, area):
    panel = panel_geometry(region, area)
    assert panel is not None
    assert area.contains(panel)
    assert not panel.intersects(region)
    assert panel.width() >= 280 and panel.height() >= 180


def test_no_space_requires_smaller_region():
    area = QRect(0, 0, 1920, 1040)
    assert panel_geometry(area, area) is None


def test_panel_title_drag_moves_window_and_refresh_keeps_position(application):
    panel = ScreenTranslationPanel()
    panel.source_region = QRect(0, 0, 120, 80)
    panel.setGeometry(150, 130, 320, 240)
    panel.show()
    application.processEvents()
    original = panel.pos()
    QTest.mousePress(panel.title, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    QTest.mouseMove(panel.title, QPoint(48, 28))
    QTest.mouseRelease(panel.title, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    assert panel.pos() != original and panel.user_moved
    moved = panel.pos()
    panel.show_translation("Hello\nWorld", "你好\n世界")
    assert panel.pos() == moved
    assert panel.translation.lineWrapMode() == panel.translation.LineWrapMode.WidgetWidth
    assert panel.minimumSize().width() <= panel.width() <= panel.maximumSize().width()
    assert panel.minimumSize().height() <= panel.height() <= panel.maximumSize().height()
    panel.close()


def test_green_region_outline_accepts_border_drag(application):
    outline = RegionOutline()
    region = QRect(100, 100, 180, 90).translated(application.primaryScreen().geometry().topLeft())
    moved = []
    outline.show_region(region)
    outline.move_finished.connect(moved.append)
    handle = outline.move_handle_rect().center()
    QTest.mousePress(outline, Qt.MouseButton.LeftButton, pos=handle)
    QTest.mouseMove(outline, handle + QPoint(20, 15))
    QTest.mouseRelease(outline, Qt.MouseButton.LeftButton, pos=handle + QPoint(20, 15))
    assert moved and moved[-1] == region.translated(20, 15)
    outline.close()


def test_green_region_outline_corner_resize_preserves_opposite_corner(application):
    outline = RegionOutline()
    region = QRect(100, 100, 180, 90).translated(application.primaryScreen().geometry().topLeft())
    moved = []
    outline.show_region(region)
    outline.move_finished.connect(moved.append)
    QTest.mousePress(outline, Qt.MouseButton.LeftButton, pos=QPoint(outline.width() - 1, outline.height() - 1))
    QTest.mouseMove(outline, QPoint(outline.width() + 39, outline.height() + 24))
    QTest.mouseRelease(outline, Qt.MouseButton.LeftButton, pos=QPoint(outline.width() + 39, outline.height() + 24))
    assert moved and moved[-1].topLeft() == region.topLeft()
    assert moved[-1].width() > region.width()
    assert moved[-1].height() > region.height()
    assert outline.mask().boundingRect() == outline.rect()
    assert not outline.mask().contains(outline.rect().center())
    outline.close()


def test_outline_resize_respects_minimum_size(application):
    outline = RegionOutline()
    outline.show_region(QRect(100, 100, 180, 90))
    QTest.mousePress(outline, Qt.MouseButton.LeftButton, pos=outline.rect().bottomRight())
    QTest.mouseMove(outline, QPoint(1, 1))
    assert outline.source_region().width() == 48
    assert outline.source_region().height() == 32
    outline.close()


def test_resizing_paused_region_keeps_pause_and_avoids_panel(reader, application):
    screen = application.primaryScreen()
    region = QRect(40, 40, 100, 60).translated(screen.geometry().topLeft())
    assert reader.start_region(screen, region)
    reader.pause()
    reader._outline_move_started()
    updated = region.adjusted(0, 0, 40, 30)
    reader._outline_move_finished(updated)
    assert reader.region == updated and not reader.state.running
    assert not reader.panel.geometry().intersects(updated)


def test_drag_clamps_to_screen_and_avoids_source():
    from PySide6.QtCore import QSize
    area = QRect(0, 0, 1920, 1080)
    source = QRect(800, 300, 500, 300)
    size = QSize(320, 240)
    for point in (QPoint(-100, -100), QPoint(1900, 1070), QPoint(850, 400)):
        result = QRect(constrain_panel(point, size, area, source), size)
        assert area.contains(result)
        assert not source.intersects(result)


def test_region_move_drag_preserves_size_and_escape_cancels(application):
    screen = application.primaryScreen()
    region = QRect(100, 100, 180, 90).translated(screen.geometry().topLeft())
    selector = RegionSelector(screen, QPixmap(800, 600), region)
    chosen, cancelled = [], []
    selector.selected.connect(lambda s, rect: chosen.append(rect))
    selector.cancelled.connect(lambda: cancelled.append(True))
    selector.show()
    QTest.mousePress(selector, Qt.MouseButton.LeftButton, pos=QPoint(130, 130))
    QTest.mouseMove(selector, QPoint(200, 200))
    QTest.mouseRelease(selector, Qt.MouseButton.LeftButton, pos=QPoint(200, 200))
    assert chosen == [region.translated(70, 70)]
    QTest.keyClick(selector, Qt.Key.Key_Escape)
    assert cancelled == [True]
    selector.close()


def test_moving_region_discards_old_requests_and_cancel_keeps_original(reader, application):
    screen = application.primaryScreen()
    source = QRect(30, 30, 160, 90).translated(screen.geometry().topLeft())
    assert reader.start_region(screen, source)
    stable(reader.state, "Old")
    token, _ = reader.state.next_request(0)
    reader.move_region()
    assert reader.selecting and not reader.state.running
    reader._cancel_selection()
    assert reader.region == source and reader.state.running
    reader.translated(token, "Old", "stale")
    assert reader.panel.translation.toPlainText() == ""
    reader.move_region()
    reader._selected(screen, source.translated(40, 90))
    assert reader.region == source.translated(40, 90)
    assert reader.state.running


def test_selector_drag_and_escape(application):
    screen = application.primaryScreen()
    selector = RegionSelector(screen, QPixmap(800, 600))
    chosen, cancelled = [], []
    selector.selected.connect(lambda s, rect: chosen.append(rect))
    selector.cancelled.connect(lambda: cancelled.append(True))
    selector.show()
    QTest.mousePress(selector, Qt.MouseButton.LeftButton, pos=QPoint(40, 50))
    QTest.mouseMove(selector, QPoint(280, 190))
    QTest.mouseRelease(selector, Qt.MouseButton.LeftButton, pos=QPoint(280, 190))
    assert chosen == [QRect(QPoint(40, 50), QPoint(280, 190)).translated(screen.geometry().topLeft())]
    QTest.keyClick(selector, Qt.Key.Key_Escape)
    assert cancelled == [True]
    selector.close()


def test_screen_hotkey_and_right_click_share_request_and_cache(reader):
    controller, profile = make_controller()
    reader.scope = lambda: profile.cache_scope
    reader.active_scope = profile.cache_scope
    controller.screen_reader = reader
    reader.translation_requested.connect(controller._translate_screen)
    controller._translate("Hello")
    reader._observe("Hello")
    reader._observe("Hello")
    assert len(controller.thread_pool.tasks) == 1
    controller._translation_succeeded(profile.cache_scope, "Hello", "你好")
    assert reader.panel.translation.toPlainText() == "你好"
    assert controller.overlay.translations[-1] == ("Hello", "你好")
    reader.pause()
    reader.resume()
    reader.state.last_request = float("-inf")
    reader._observe("Hello")
    reader._observe("Hello")
    controller._translate("Hello")
    assert len(controller.thread_pool.tasks) == 1
    assert reader.panel.translation.toPlainText() == "你好"


def test_changed_ocr_text_rejects_old_translation(reader):
    stable(reader.state, "Old")
    token, _ = reader.state.next_request(0)
    reader._observe("New")
    reader.translated(token, "Old", "obsolete")
    assert reader.panel.translation.toPlainText() == ""


def test_scope_change_invalidates_inflight_result(reader):
    stable(reader.state, "Hello")
    token, _ = reader.state.next_request(0)
    reader.scope = lambda: "new-provider"
    reader.translated(token, "Hello", "obsolete")
    assert reader.panel.translation.toPlainText() == ""
    assert reader.state.pending is None


def test_error_pauses_automatic_requests_and_resume_can_retry(reader):
    requests = []
    reader.translation_requested.connect(lambda text, token: requests.append((text, token)))
    reader._observe("Hello")
    reader._observe("Hello")
    reader.translated(requests[0][1], "Hello", "HTTP 401", error=True)
    assert not reader.state.running
    reader._observe("Hello")
    reader._observe("Hello")
    assert len(requests) == 1
    assert "401" in reader.panel.status.text()
    reader.resume()
    reader.state.last_request = float("-inf")
    reader._observe("Hello")
    reader._observe("Hello")
    assert len(requests) == 2


def test_old_error_does_not_pause_new_session(reader):
    stable(reader.state, "Hello")
    token, _ = reader.state.next_request(0)
    reader.state.reset(True)
    reader.translated(token, "Hello", "old error", error=True)
    assert reader.state.running


@pytest.mark.parametrize("text", ["", "sk-abcdefghijklmnopqrstuvwx", "x" * 5001])
def test_invalid_or_sensitive_text_never_sends_request(reader, text):
    requests = []
    reader.translation_requested.connect(lambda *args: requests.append(args))
    reader._observe(text)
    reader._observe(text)
    assert requests == []


def test_ocr_completion_after_pause_is_ignored(reader):
    reader.ocr_job = (1, reader.state.generation)
    reader.pause()
    reader._recognized(1, b"digest", "Old")
    assert reader.ocr_job is None
    assert reader.state.text == ""


def test_unchanged_frame_reuses_ocr_result(reader, application, monkeypatch):
    from PySide6.QtGui import QImage
    import hashlib
    class Screen:
        def geometry(self):
            return QRect(0, 0, 1920, 1080)
        def devicePixelRatio(self):
            return 1.0
        def grabWindow(self, *args):
            return QPixmap.fromImage(image)
    image = QImage(100, 60, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    screen = Screen()
    reader.screen = screen
    reader.screen_bounds = screen.geometry()
    reader.frame_digest = hashlib.sha256(image.constBits()).digest()
    reader.frame_text = "Hello"
    monkeypatch.setattr(QApplication, "screens", lambda: [screen])
    monkeypatch.setattr(QApplication, "topLevelWidgets", lambda: [])
    reader.tick()
    reader.tick()
    assert reader.ocr_job is None
    assert reader.state.observations == 2
    assert reader.state.pending[1] == "Hello"
