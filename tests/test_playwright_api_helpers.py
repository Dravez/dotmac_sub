from __future__ import annotations

import pytest
from playwright.sync_api import Error as PlaywrightError

from tests.playwright.helpers import api


class _RetryingGetContext:
    def __init__(self, response: object) -> None:
        self.calls = 0
        self.response = response

    def get(self, url: str, *, headers: dict[str, str]) -> object:
        self.calls += 1
        if self.calls == 1:
            raise PlaywrightError("read ECONNRESET")
        return self.response


class _FailingGetContext:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, url: str, *, headers: dict[str, str]) -> object:
        self.calls += 1
        raise PlaywrightError("read ECONNRESET")


def test_api_get_retries_transient_playwright_errors(monkeypatch) -> None:
    response = object()
    context = _RetryingGetContext(response)
    monkeypatch.setattr(api.time, "sleep", lambda _seconds: None)

    result = api.api_get(context, "/api/v1/customers/search", headers={"X": "1"})

    assert result is response
    assert context.calls == 2


def test_api_get_reraises_after_three_transient_errors(monkeypatch) -> None:
    context = _FailingGetContext()
    monkeypatch.setattr(api.time, "sleep", lambda _seconds: None)

    with pytest.raises(PlaywrightError, match="ECONNRESET"):
        api.api_get(context, "/api/v1/customers/search")

    assert context.calls == 3
