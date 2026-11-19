"""Microsoft Translator backend against a fake connection: the request it sends, the answer it
reads, a bad key, and a kept-alive connection the server has closed."""

import http.client
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture.translate import AzureBackend, TranslationUnavailable, make_backend  # noqa: E402


class FakeConnection:
    def __init__(self, status=200, fail=False):
        self.status, self.fail, self.sent, self.closed = status, fail, [], False

    def request(self, method, path, body, headers):
        if self.fail:
            raise http.client.RemoteDisconnected("closed by the server")
        self.sent.append((method, path, json.loads(body), dict(headers)))

    def getresponse(self):
        conn = self

        class Response:
            status = conn.status

            def read(self):
                if conn.status != 200:
                    return b'{"error": {"code": 401000}}'
                return json.dumps([{"translations": [{"text": "大家早上好。", "to": "zh-Hans"}]}]).encode()

        return Response()

    def close(self):
        self.closed = True


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "test-key")
    monkeypatch.delenv("AZURE_TRANSLATOR_REGION", raising=False)
    monkeypatch.setattr("livecapture.keys.user_env", lambda name: None)
    return lambda region="": make_backend("azure-translator", None, region)


def use(b: AzureBackend, *conns):
    """The backend's connections, in order: the next one each time it has dropped the last."""
    pending = list(conns)

    def connection():
        if getattr(b._local, "conn", None) is None:  # noqa: SLF001
            b._local.conn = pending.pop(0)  # noqa: SLF001
        return b._local.conn  # noqa: SLF001

    b._connection = connection  # noqa: SLF001


def test_sends_one_sentence_and_reads_the_translation(backend):
    b = backend("eastasia")
    c = FakeConnection()
    use(b, c)
    assert b("Guten Morgen zusammen.", "de", "zh", context=[("other", "a", "b")]) == "大家早上好。"
    method, path, body, headers = c.sent[0]
    assert method == "POST" and path.startswith("/translate?") and "to=zh-Hans" in path and "from=" not in path
    assert body == [{"Text": "Guten Morgen zusammen."}]  # no context: the service has none
    assert headers["Ocp-Apim-Subscription-Key"] == "test-key" and headers["Ocp-Apim-Subscription-Region"] == "eastasia"
    assert b.usage["chars"] == len("Guten Morgen zusammen.")


def test_a_global_resource_sends_no_region_and_chinese_is_named(backend):
    b = backend("global")
    c = FakeConnection()
    use(b, c)
    b("大家早上好。", "zh", "en", context=[])
    _, path, _, headers = c.sent[0]
    assert "Ocp-Apim-Subscription-Region" not in headers and "from=zh-Hans" in path and "to=en" in path


def test_a_bad_key_stops_translation(backend):
    b = backend()
    use(b, FakeConnection(status=401))
    with pytest.raises(TranslationUnavailable):
        b("Hallo.", "de", "zh", context=[])


def test_a_closed_connection_is_replaced_once(backend):
    b = backend()
    stale, fresh = FakeConnection(fail=True), FakeConnection()
    use(b, stale, fresh)
    assert b("Hallo.", "de", "zh", context=[]) == "大家早上好。"
    assert stale.closed and len(fresh.sent) == 1


def test_no_key_no_backend(monkeypatch):
    monkeypatch.delenv("AZURE_TRANSLATOR_KEY", raising=False)
    monkeypatch.setattr("livecapture.keys.user_env", lambda name: None)
    monkeypatch.setattr("livecapture.keys.saved", lambda provider: None)
    with pytest.raises(TranslationUnavailable):
        make_backend("azure-translator")
