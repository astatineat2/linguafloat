import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


HELPER = r'''
import os, sys, time
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget
from linguafloat.single_instance import SingleInstance
root, marker, delay = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
if os.environ.get('LF_TEST_DEFAULT_IDENTITY'):
    import linguafloat.single_instance as module
    real_identity = module._windows_identity
    module._windows_identity = lambda: real_identity() + str(root)
    module.QStandardPaths.writableLocation = lambda _: os.environ['LF_TEST_DATA_PATH']
    guard = SingleInstance()
else:
    guard = SingleInstance(root)
try:
    if guard.start(timeout_ms=int(sys.argv[4])):
        window = QWidget()
        window.hide()
        def activate():
            window.showNormal()
            with (root / 'activations').open('a') as output:
                output.write(str(window.isVisible()) + '\n')
        guard.activation_requested.connect(activate)
        marker.write_text('primary')
        time.sleep(delay)
        timer = QTimer()
        timer.timeout.connect(lambda: app.quit() if (root / 'stop').exists() else None)
        timer.start(20)
        QTimer.singleShot(20000, app.quit)
        app.exec()
    else:
        marker.write_text('secondary')
finally:
    guard.close()
'''


def wait_until(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.03)
    raise AssertionError('Timed out waiting for child process')


@pytest.fixture
def launch(tmp_path):
    processes = []

    def start(delay=0, timeout_ms=8000, default_identity=False):
        marker = tmp_path / f'process-{len(processes)}'
        process = subprocess.Popen(
            [sys.executable, '-c', HELPER, str(tmp_path), str(marker), str(delay), str(timeout_ms)],
            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen',
                 'PYTHONIOENCODING': 'utf-8',
                 'LF_TEST_DEFAULT_IDENTITY': '1' if default_identity else '',
                 'LF_TEST_DATA_PATH': str(tmp_path / f'virtualized-{len(processes)}'),
                 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        )
        processes.append(process)
        return process, marker

    yield start
    (tmp_path / 'stop').touch()
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.communicate()


def assert_secondary(process, marker):
    stdout, stderr = process.communicate(timeout=12)
    assert process.returncode == 0, stdout + stderr
    assert marker.read_text() == 'secondary'


def test_second_launch_restores_hidden_window(launch, tmp_path):
    primary, marker = launch()
    wait_until(lambda: marker.exists() and marker.stat().st_size > 0)
    assert marker.read_text() == 'primary'
    assert_secondary(*launch())
    assert (tmp_path / 'activations').read_text().splitlines() == ['True']
    assert primary.poll() is None


def test_simultaneous_launches_have_only_one_owner(launch, tmp_path):
    children = [launch(delay=.6) for _ in range(5)]
    wait_until(lambda: all(marker.exists() and marker.stat().st_size > 0 for _, marker in children))
    owners = [(process, marker) for process, marker in children if marker.read_text() == 'primary']
    assert len(owners) == 1
    for process, marker in children:
        if marker.read_text() == 'secondary':
            assert_secondary(process, marker)
    assert len((tmp_path / 'activations').read_text().splitlines()) == 4


@pytest.mark.parametrize('crash', [False, True])
def test_can_restart_after_exit_or_crash(launch, tmp_path, crash):
    primary, marker = launch()
    wait_until(lambda: marker.exists() and marker.stat().st_size > 0)
    if crash:
        primary.kill()
    else:
        (tmp_path / 'stop').touch()
    primary.wait(timeout=5)
    (tmp_path / 'stop').unlink(missing_ok=True)
    restarted, marker = launch()
    wait_until(lambda: marker.exists() and marker.stat().st_size > 0)
    assert marker.read_text() == 'primary'
    assert_secondary(*launch())
    assert restarted.poll() is None


def test_activation_waits_for_initial_startup(launch, tmp_path):
    primary, marker = launch(delay=1.2)
    wait_until(lambda: marker.exists() and marker.stat().st_size > 0)
    assert_secondary(*launch())
    assert (tmp_path / 'activations').read_text().strip() == 'True'
    assert primary.poll() is None


def test_unresponsive_owner_does_not_allow_second_instance(launch):
    primary, marker = launch(delay=1.5)
    wait_until(lambda: marker.exists() and marker.stat().st_size > 0)
    secondary, marker = launch(timeout_ms=150)
    _, stderr = secondary.communicate(timeout=5)
    assert secondary.returncode != 0
    assert 'SingleInstanceError' in stderr
    assert not marker.exists()
    assert primary.poll() is None
    assert_secondary(*launch())


@pytest.mark.skipif(os.name != 'nt', reason='Windows package path redirection')
def test_default_identity_ignores_virtualized_data_directories(launch, tmp_path):
    primary, marker = launch(default_identity=True)
    wait_until(lambda: marker.exists() and marker.stat().st_size > 0)
    assert marker.read_text() == 'primary'
    assert_secondary(*launch(default_identity=True))
    assert (tmp_path / 'activations').read_text().splitlines() == ['True']
    assert not list(tmp_path.glob('virtualized-*'))
    assert primary.poll() is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows named mutex')
def test_missing_lock_file_does_not_allow_duplicate(launch, tmp_path):
    primary, marker = launch()
    wait_until(lambda: marker.exists() and marker.stat().st_size > 0)
    assert not (tmp_path / 'application.lock').exists()
    assert_secondary(*launch())
    assert primary.poll() is None
