from types import SimpleNamespace

import httpx
import pytest
from openai import AuthenticationError, PermissionDeniedError

from linguafloat.config import ProviderProfile
from linguafloat.translation import OpenAITranslator, TranslationCache, TranslationError


def profile(api_mode="responses"):
    return ProviderProfile(
        id="example",
        name="Example",
        base_url="https://proxy.example.com/v1",
        model="example-model",
        api_mode=api_mode,
    )


class FakeResponses:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_text="  你好，世界！  ")


class FakeChatCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(content="  你好！  ")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_translator_uses_responses_api_and_strips_output():
    responses = FakeResponses()
    client = SimpleNamespace(responses=responses)
    translator = OpenAITranslator("unused", profile(), client=client)

    result = translator.translate("Hello, world!")

    assert result == "你好，世界！"
    assert responses.kwargs["model"] == "example-model"
    assert responses.kwargs["input"] == "Hello, world!"


def test_translator_supports_chat_completions():
    completions = FakeChatCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    translator = OpenAITranslator(
        "unused", profile("chat_completions"), client=client
    )

    result = translator.translate("Hello!")

    assert result == "你好！"
    assert completions.kwargs["model"] == "example-model"
    assert completions.kwargs["messages"][1]["content"] == "Hello!"


def test_translation_cache_evicts_oldest_item():
    cache = TranslationCache(capacity=2)
    cache.put("provider", "one", "一")
    cache.put("provider", "two", "二")
    cache.put("provider", "three", "三")

    assert cache.get("provider", "one") is None
    assert cache.get("provider", "two") == "二"
    assert cache.get("provider", "three") == "三"


@pytest.mark.parametrize("language,expected", [("en", "English"), ("ko", "Korean"), ("ja", "Japanese")])
@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
def test_explicit_target_language_is_sent_to_both_api_types(language, expected, mode):
    responses = FakeResponses()
    completions = FakeChatCompletions()
    client = SimpleNamespace(responses=responses, chat=SimpleNamespace(completions=completions))
    OpenAITranslator("dummy", profile(mode), client=client, target_language=language).translate("你好")
    instruction = responses.kwargs["instructions"] if mode == "responses" else completions.kwargs["messages"][0]["content"]
    assert expected in instruction
    assert "If it is Simplified" not in instruction


def test_multiline_translation_is_returned_as_exactly_corresponding_lines():
    responses = FakeResponses()
    def create(**kwargs):
        responses.kwargs = kwargs
        return SimpleNamespace(output_text='["你好", "", "世界"]')
    responses.create = create
    client = SimpleNamespace(responses=responses)
    result = OpenAITranslator("dummy", profile(), client=client).translate("Hello\n\nWorld")
    assert result == "你好\n\n世界"
    assert "exactly 3 strings" in responses.kwargs["instructions"]


@pytest.mark.parametrize("raw", ['["one"]', '["one", 2]', 'not JSON', '["one\\ntwo", "three"]'])
def test_invalid_line_response_does_not_silently_misalign(raw):
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(output_text=raw)))
    with pytest.raises(TranslationError):
        OpenAITranslator("dummy", profile(), client=client).translate("一\n二")


@pytest.mark.parametrize("status,error_type", [
    (401, AuthenticationError), (403, PermissionDeniedError),
])
def test_auth_errors_include_provider_and_status_without_echoing_secrets(status, error_type):
    secret = "dummy-private-key"
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.com/v1/responses"))
    def create(**kwargs):
        raise error_type(f"Rejected key: {secret}", response=response, body={"key": secret})
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    with pytest.raises(TranslationError) as caught:
        OpenAITranslator(secret, profile(), client=client).translate("Hello")
    assert str(status) in str(caught.value)
    assert "Example" in str(caught.value)
    assert secret not in str(caught.value)


def test_unexpected_api_exception_does_not_leak_response_or_key():
    def create(**kwargs):
        raise ValueError("Authorization: Bearer dummy-private-key")
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    with pytest.raises(TranslationError) as caught:
        OpenAITranslator("dummy-private-key", profile(), client=client).translate("Hello")
    assert "dummy-private-key" not in str(caught.value)
