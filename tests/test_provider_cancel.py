"""Cancellation tests use local workers and fake clients, never a service."""
from threading import Event, Thread
from types import SimpleNamespace

from PySide6.QtCore import QRunnable, QThreadPool

from linguafloat.config import ProviderProfile
from linguafloat.provider_tools import ProviderTask
import linguafloat.provider_tools as module
from test_credentials_ui import application


def profile():
    return ProviderProfile("test", "Test", "https://example.test/v1", "model")


def test_cancelled_queued_task_never_creates_a_client(application, monkeypatch):
    started, release = Event(), Event()
    clients = []

    class Blocker(QRunnable):
        def run(self):
            started.set()
            release.wait(3)

    def unexpected_client(**kwargs):
        clients.append(True)
        raise AssertionError("Cancelled task tried to create a client")

    monkeypatch.setattr(module, "OpenAI", unexpected_client)
    pool = QThreadPool(); pool.setMaxThreadCount(1)
    blocker = Blocker(); pool.start(blocker)
    try:
        assert started.wait(1)
        task = ProviderTask(1, profile(), "synthetic", "models")
        pool.start(task)
        task.cancel()
        assert task.cancelled and task.key == ""
    finally:
        release.set()
        assert pool.waitForDone(3000)
    assert clients == []


def test_cancel_closes_active_model_client_and_suppresses_result(application, monkeypatch):
    started, released = Event(), Event()
    results, clients = [], []

    class Client:
        def __init__(self, **kwargs):
            self.models = self
            self.closed = False
            clients.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def list(self):
            started.set()
            assert released.wait(3)
            return SimpleNamespace(data=[SimpleNamespace(id="late-result")])

        def close(self):
            self.closed = True
            released.set()

    monkeypatch.setattr(module, "OpenAI", Client)
    task = ProviderTask(1, profile(), "synthetic", "models")
    task.signals.finished.connect(lambda *args: results.append(args))
    worker = Thread(target=task.run)
    worker.start()
    try:
        assert started.wait(1)
        task.cancel()
    finally:
        released.set()
        worker.join(3)
    application.processEvents()
    assert not worker.is_alive()
    assert clients[0].closed and task.key == "" and results == []


def test_cancel_during_client_creation_prevents_dispatch(application, monkeypatch):
    clients = []
    task = ProviderTask(1, profile(), "synthetic", "models")

    class Client:
        def __init__(self, **kwargs):
            self.closed = False
            clients.append(self)
            task.cancel()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

    monkeypatch.setattr(module, "OpenAI", Client)
    task.run()
    assert clients[0].closed and task.key == "" and task.cancelled
