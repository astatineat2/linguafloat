import gc,hashlib,threading,time,weakref
from PySide6.QtCore import QRect,Qt,QPoint,QTimer
from PySide6.QtGui import QImage,QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from test_screen import application,reader
from test_credentials_ui import controller
from linguafloat.ocr import OcrTask,LocalOcr,ReleaseOcrTask
from linguafloat.screen_ui import RegionSelector


def test_selection_needs_no_screen_snapshot(reader,application,monkeypatch):
    screen=application.primaryScreen()
    monkeypatch.setattr(type(screen),'grabWindow',lambda *a:(_ for _ in ()).throw(AssertionError('snapshot not allowed')))
    reader.select_region();QTest.qWait(40)
    assert reader.selectors and not hasattr(reader.selectors[0],'snapshot')
    reader.stop();assert not reader.selectors and not reader.outline.isVisible()


def test_interior_click_stays_and_drag_preserves_size(reader,application):
    o=reader.outline;r=QRect(100,100,180,90);reader.start_region(application.primaryScreen(),r);application.processEvents()
    p=o.rect().center();QTest.mouseClick(o,Qt.LeftButton,pos=p);assert o.source_region()==r
    QTest.mousePress(o,Qt.LeftButton,pos=p);QTest.mouseMove(o,p+QPoint(20,15));QTest.mouseRelease(o,Qt.LeftButton,pos=p)
    assert o.source_region()==r.translated(20,15)
    assert o.mask().contains(o.rect().center())


def test_shutdown_during_slow_ocr_returns_without_blocking(reader,application):
    entered=threading.Event();release=threading.Event()
    class Engine:
        engine=True;last_regions=[]
        def recognize(self,image,**kwargs):entered.set();release.wait(2);return 'late'
    image=QImage(80,40,QImage.Format_RGB32);image.fill(Qt.white)
    job=OcrTask(Engine(),image,1,b'digest');reader.ocr_job=(1,reader.state.generation)
    job.signals.succeeded.connect(reader._recognized);reader.pool.start(job)
    assert entered.wait(1)
    t=time.perf_counter();assert not reader.shutdown();assert time.perf_counter()-t<.2
    assert not reader.outline.isVisible() and not reader.panel.isVisible()
    release.set();reader.pool.waitForDone(2000);QTest.qWait(30)
    assert not reader.state.running and not reader.frame_text and reader.ocr_job is None


def test_stale_progress_cannot_replace_pause(reader):
    reader.ocr_job=(1,reader.state.generation);reader.pause();before=reader.panel.status.text()
    reader._recognition_phase(1,'old progress');assert reader.panel.status.text()==before


def test_same_image_ocr_cache_survives_stop_but_not_idle_release(reader,application,monkeypatch):
    image=QImage(100,60,QImage.Format_RGB32);image.fill(Qt.white)
    h=hashlib.sha256(f'{image.width()}:{image.height()}:{image.format().value}:'.encode());h.update(image.constBits())
    reader.ocr_job=(1,reader.state.generation);reader._recognized(1,h.digest(),'Hello')
    reader.stop();assert reader._ocr_cache is not None
    class Screen:
        def geometry(self):return QRect(0,0,1920,1080)
        def availableGeometry(self):return self.geometry()
        def devicePixelRatio(self):return 1.
        def grabWindow(self,*a):return QPixmap.fromImage(image)
    screen=Screen();reader.start_region(screen,QRect(30,30,100,60));reader.settle_timer.stop()
    monkeypatch.setattr(QApplication,'screens',lambda:[screen]);monkeypatch.setattr(QApplication,'topLevelWidgets',lambda:[])
    monkeypatch.setattr(reader.engine,'recognize',lambda *a,**k:(_ for _ in ()).throw(AssertionError('cached frame must not run OCR')))
    reader.tick();assert reader.ocr_job is None and reader.frame_text=='Hello'
    reader.pause();reader._release_idle_ocr();reader.pool.waitForDone(2000)
    assert reader._ocr_cache is None and reader.engine.engine is None


def test_release_obsolete_after_resume(reader):
    marker=object();reader.engine.engine=marker;reader.pause();generation=reader.state.generation
    task=ReleaseOcrTask(reader.engine,lambda:not reader.state.running and reader.state.generation==generation)
    reader.resume();task.run();assert reader.engine.engine is marker


def test_diagnostic_and_region_share_serial_engine(controller):
    c,_,_=controller
    assert c._diagnostic_ocr is c.screen_reader.engine
    assert c.screen_reader.pool.maxThreadCount()==1


def test_application_quit_waits_by_timer_not_ui_block(controller,monkeypatch):
    c,_,_=controller;entered=threading.Event();release=threading.Event();closed=[]
    class Engine:
        last_regions=[]
        def recognize(self,image,**kwargs):entered.set();release.wait(2);return 'late'
    task=OcrTask(Engine(),QImage(40,40,QImage.Format_RGB32),1,b'')
    c.screen_reader.pool.start(task);assert entered.wait(1)
    monkeypatch.setattr(c.application,'quit',lambda:closed.append(True))
    start=time.perf_counter();c.quit(for_update=True)
    assert time.perf_counter()-start<.2 and not closed
    release.set();QTest.qWait(200)
    assert closed and not c.screen_reader.outline.isVisible()


def test_detector_tensors_do_not_wait_for_cyclic_gc(application):
    from PIL import Image,ImageDraw,ImageFont
    canvas=Image.new('RGB',(740,180),'white')
    ImageDraw.Draw(canvas).text((15,25),'Hello world. Screen translation is ready.',fill='black',font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',28))
    data=canvas.tobytes();image=QImage(data,740,180,2220,QImage.Format_RGB888)
    engine=LocalOcr();expected=engine.recognize(image);detector=engine.engine.text_det
    assert isinstance(detector.mean,list) and isinstance(detector.std,list)
    session=detector.session;references=[]
    def infer(tensor):
        references.append(weakref.ref(tensor));return session(tensor)
    detector.session=infer;gc.collect();enabled=gc.isenabled();gc.disable()
    try:
        for _ in range(3):assert engine.recognize(image)==expected
        assert references and all(ref() is None for ref in references)
    finally:
        if enabled:gc.enable()
        detector.session=session
