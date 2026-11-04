from linguafloat.app import AppController
from linguafloat.config import AppSettings, ProviderProfile
from linguafloat.translation import TranslationCache
from types import SimpleNamespace


class FakeCredentials:
    def get_api_key(self, provider_id, env_key):
        return "test-key"


class FakeOverlay:
    def __init__(self):
        self.loading = []
        self.translations = []
        self.errors = []
        self.origin = "selection"
        self.source_text = ""

    def show_loading(self, source_text, target_language="auto", *, origin="selection"):
        self.source_text = source_text
        self.origin = origin
        self.loading.append(source_text)

    def show_translation(self, source_text, translated_text, target_language="auto", *, origin="selection"):
        self.source_text = source_text
        self.origin = origin
        self.translations.append((source_text, translated_text))

    def target_language(self):
        return "auto"

    def show_error(self, message):
        self.errors.append(message)


class FakeWindow:
    def __init__(self):
        self.activities = []

    def set_activity(self, message, *, error=False):
        self.activities.append((message, error))

    def bring_to_front(self):
        pass


class FakeThreadPool:
    def __init__(self):
        self.tasks = []

    def start(self, task):
        self.tasks.append(task)


def make_controller():
    controller = AppController.__new__(AppController)
    profile = ProviderProfile(
        id="test",
        name="Test",
        base_url="https://example.com/v1",
        model="test-model",
    )
    controller.settings = AppSettings(
        active_provider=profile.id,
        providers=[profile],
    )
    controller.credentials = FakeCredentials()
    controller.cache = TranslationCache()
    controller._inflight_requests = set()
    controller._request_targets = {}
    controller.paused = False
    controller.game_active = False
    controller._trigger_error = ""
    controller._ordinary_generation = 0
    controller.game_monitor = SimpleNamespace(refresh=lambda: None)
    controller.overlay = FakeOverlay()
    controller.window = FakeWindow()
    controller.thread_pool = FakeThreadPool()
    return controller, profile


def test_hotkey_and_right_click_requests_share_inflight_guard_and_cache():
    controller, profile = make_controller()

    # The hotkey capture and right-click button both connect to this same method.
    controller._translate("Hello")
    controller._translate("  Hello  ")

    assert len(controller.thread_pool.tasks) == 1
    assert controller.window.activities[-1][0] == "相同内容正在翻译，请稍候。"

    controller._translation_succeeded(profile.cache_scope, "Hello", "你好")
    controller._translate("Hello")

    assert len(controller.thread_pool.tasks) == 1
    assert controller.overlay.translations[-1] == ("Hello", "你好")
    assert controller.window.activities[-1][0] == "已从本地缓存读取翻译。"


def test_failed_request_is_removed_from_inflight_guard():
    controller, profile = make_controller()
    controller._translate("Hello")

    controller._translation_failed(profile.cache_scope, "Hello", "failed")
    controller._translate("Hello")

    assert len(controller.thread_pool.tasks) == 2
