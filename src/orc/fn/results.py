from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import reduce
from typing import ParamSpec, final

from orc.fn.result_combinators import ResultCombinators

type WithErrors[A] = Result[Sequence[str], A]
P = ParamSpec("P")


class Result[E, A](ABC, ResultCombinators):
    @abstractmethod
    def map[B](self, f: Callable[[A], B]) -> Result[E, B]: ...

    @abstractmethod
    def flat_map[B, E1](self, f: Callable[[A], Result[E1, B]]) -> Result[E | E1, B]: ...

    def then[B, E1](self, f: Callable[[], Result[E1, B]]) -> Result[E | E1, B]:
        return self.flat_map(lambda _: f())

    @abstractmethod
    def map_error[E1](self, f: Callable[[E], E1]) -> Result[E1, A]: ...

    @abstractmethod
    def or_else[E1](self, f: Callable[[E], Result[E1, A]]) -> Result[E1, A]: ...

    @abstractmethod
    def get_or_else(self, default: A) -> A: ...  # pyright: ignore[reportGeneralTypeIssues]  # covariant A in parameter: safe, Result is immutable

    @abstractmethod
    def on_error(self, f: Callable[[E], A]) -> A: ...

    @abstractmethod
    def fold[B](self, on_error: Callable[[E], B], on_success: Callable[[A], B]) -> B: ...

    def tap(self, f: Callable[[], None]) -> Result[E, None]:
        return self.map(lambda _: f())

    @staticmethod
    def validate[E1, A1](
        value: A1, validation: Callable[[A1], bool], on_error: Callable[[A1], E1]
    ) -> Result[E1, A1]:
        return Ok(value) if validation(value) else Err(on_error(value))

    @staticmethod
    def chain[E1](*thunks: Callable[[], Result[E1, None]]) -> Result[E1, None]:
        return reduce(
            lambda acc, thunk: acc.flat_map(lambda _: thunk()),
            thunks,
            Ok(None),
        )

    @staticmethod
    def sequence[E1, A1](
        values: Sequence[Result[Sequence[E1], A1]],
    ) -> Result[Sequence[E1], Sequence[A1]]:
        return Result.traverse(values, lambda x: x)

    @staticmethod
    def traverse[E1, A1, B1](
        values: Sequence[A1], f: Callable[[A1], Result[Sequence[E1], B1]]
    ) -> Result[Sequence[E1], Sequence[B1]]:
        errors: list[E1] = []
        results: list[B1] = []
        for a in values:
            f(a).fold(errors.extend, results.append)
        return Err(errors) if errors else Ok(results)

    @staticmethod
    def safe[E1, A1](run: Callable[[], A1], on_error: Callable[[Exception], E1]) -> Result[E1, A1]:
        try:
            return Ok(run())
        except Exception as e:
            return Err(on_error(e))

    @staticmethod
    def lift_safe[**P, E1, A1](
        f: Callable[P, A1], on_error: Callable[[Exception], E1]
    ) -> Callable[P, Result[E1, A1]]:
        def inner(*args: P.args, **kwargs: P.kwargs) -> Result[E1, A1]:
            return Result.safe(lambda: f(*args, **kwargs), on_error)

        return inner

    @staticmethod
    def optional[E1, A1](value: A1 | None, on_missing: Callable[[], E1]) -> Result[E1, A1]:
        return Ok(value) if value is not None else Err(on_missing())


@final
@dataclass(frozen=True)
class Ok[E, A](Result[E, A]):
    value: A

    def map[B](self, f: Callable[[A], B]) -> Result[E, B]:
        return Ok(f(self.value))

    def flat_map[B, E1](self, f: Callable[[A], Result[E1, B]]) -> Result[E | E1, B]:
        return f(self.value)

    def map_error[E1](self, f: Callable[[E], E1]) -> Result[E1, A]:
        return Ok(self.value)

    def or_else[E1](self, f: Callable[[E], Result[E1, A]]) -> Result[E1, A]:
        return Ok(self.value)

    def get_or_else(self, default: A) -> A:  # pyright: ignore[reportGeneralTypeIssues]  # covariant A in parameter: safe, frozen dataclass
        return self.value

    def on_error(self, f: Callable[[E], A]) -> A:
        return self.value

    def fold[B](self, on_error: Callable[[E], B], on_success: Callable[[A], B]) -> B:
        return on_success(self.value)


@final
@dataclass(frozen=True)
class Err[E, A](Result[E, A]):
    error: E

    def map[B](self, f: Callable[[A], B]) -> Result[E, B]:
        return Err(self.error)

    def flat_map[B, E1](self, f: Callable[[A], Result[E1, B]]) -> Result[E | E1, B]:
        return Err(self.error)

    def map_error[E1](self, f: Callable[[E], E1]) -> Result[E1, A]:
        return Err(f(self.error))

    def or_else[E1](self, f: Callable[[E], Result[E1, A]]) -> Result[E1, A]:
        return f(self.error)

    def get_or_else(self, default: A) -> A:  # pyright: ignore[reportGeneralTypeIssues]  # covariant A in parameter: safe, frozen dataclass
        return default

    def on_error(self, f: Callable[[E], A]) -> A:
        return f(self.error)

    def fold[B](self, on_error: Callable[[E], B], on_success: Callable[[A], B]) -> B:
        return on_error(self.error)
