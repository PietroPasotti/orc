"""Tests for orc/retry.py — retry decorator and retry_call helper."""

from __future__ import annotations

import pytest

from orc.engine.retry import retry, retry_call


class TestRetryDecorator:
    def test_success_on_first_attempt(self):
        calls = []

        @retry(max_attempts=3)
        def fn():
            calls.append(1)
            return "ok"

        result = fn()
        assert result == "ok"
        assert len(calls) == 1

    def test_retries_on_failure_then_succeeds(self):
        calls = []

        @retry(max_attempts=3, initial_delay=0)
        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ValueError("not yet")
            return "done"

        result = fn()
        assert result == "done"
        assert len(calls) == 3

    def test_raises_after_max_attempts(self):
        calls = []

        @retry(max_attempts=2, initial_delay=0)
        def fn():
            calls.append(1)
            raise RuntimeError("always fails")

        with pytest.raises(RuntimeError, match="always fails"):
            fn()
        assert len(calls) == 2

    def test_only_retries_matching_exceptions(self):
        calls = []

        @retry(max_attempts=3, exceptions=(ValueError,), initial_delay=0)
        def fn():
            calls.append(1)
            raise TypeError("wrong type")

        with pytest.raises(TypeError):
            fn()
        assert len(calls) == 1  # no retry for non-matching exception

    def test_passes_args_and_kwargs(self):
        @retry(max_attempts=1)
        def add(a, b=0):
            return a + b

        assert add(3, b=4) == 7

    def test_preserves_function_name(self):
        @retry()
        def my_function():
            pass

        assert my_function.__name__ == "my_function"

    def test_backoff_increases_delay(self, monkeypatch):
        delays = []

        monkeypatch.setattr("time.sleep", lambda d: delays.append(d))

        @retry(max_attempts=3, initial_delay=1.0, backoff_factor=2.0)
        def fn():
            raise ValueError("fail")

        with pytest.raises(ValueError):
            fn()

        assert delays == [1.0, 2.0]

    def test_max_attempts_one_means_no_retry(self):
        calls = []

        @retry(max_attempts=1, initial_delay=0)
        def fn():
            calls.append(1)
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            fn()
        assert len(calls) == 1


class TestRetryCall:
    def test_success(self):
        result = retry_call(lambda: 42, max_attempts=1)
        assert result == 42

    def test_retries_and_succeeds(self):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) < 2:
                raise OSError("fail")
            return "ok"

        result = retry_call(fn, max_attempts=3, initial_delay=0)
        assert result == "ok"
        assert len(calls) == 2

    def test_raises_after_exhaustion(self):
        with pytest.raises(OSError):
            retry_call(
                lambda: (_ for _ in ()).throw(OSError("always")),
                max_attempts=2,
                initial_delay=0,
            )
