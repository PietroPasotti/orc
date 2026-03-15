from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orc.fn.results import Result


class CombineUsing2[E, A, B]:
    def __init__(
        self,
        r1: Result[Sequence[E], A],
        r2: Result[Sequence[E], B],
    ) -> None:
        self._r1 = r1
        self._r2 = r2

    def using[C](self, f: Callable[[A, B], C]) -> Result[Sequence[E], C]:
        return ResultCombinators.map2(f, self._r1, self._r2)


class CombineUsing3[E, A, B, C]:
    def __init__(
        self,
        r1: Result[Sequence[E], A],
        r2: Result[Sequence[E], B],
        r3: Result[Sequence[E], C],
    ) -> None:
        self._r1 = r1
        self._r2 = r2
        self._r3 = r3

    def using[D](self, f: Callable[[A, B, C], D]) -> Result[Sequence[E], D]:
        return ResultCombinators.map3(f, self._r1, self._r2, self._r3)


class CombineUsing4[E, A, B, C, D]:
    def __init__(
        self,
        r1: Result[Sequence[E], A],
        r2: Result[Sequence[E], B],
        r3: Result[Sequence[E], C],
        r4: Result[Sequence[E], D],
    ) -> None:
        self._r1 = r1
        self._r2 = r2
        self._r3 = r3
        self._r4 = r4

    def using[G](self, f: Callable[[A, B, C, D], G]) -> Result[Sequence[E], G]:
        return ResultCombinators.map4(f, self._r1, self._r2, self._r3, self._r4)


class CombineUsing5[E, A, B, C, D, F]:
    def __init__(
        self,
        r1: Result[Sequence[E], A],
        r2: Result[Sequence[E], B],
        r3: Result[Sequence[E], C],
        r4: Result[Sequence[E], D],
        r5: Result[Sequence[E], F],
    ) -> None:
        self._r1 = r1
        self._r2 = r2
        self._r3 = r3
        self._r4 = r4
        self._r5 = r5

    def using[G](self, f: Callable[[A, B, C, D, F], G]) -> Result[Sequence[E], G]:
        return ResultCombinators.map5(f, self._r1, self._r2, self._r3, self._r4, self._r5)


class ResultCombinators:
    @staticmethod
    def zip[E, A, B](
        r1: Result[Sequence[E], A], r2: Result[Sequence[E], B]
    ) -> Result[Sequence[E], tuple[A, B]]:
        from orc.fn.results import Err, Ok  # lazy import to avoid circular dependency

        # a shame we can't have exhaustive pattern matching on abstract classes...
        return r1.fold(
            lambda e1: r2.fold(
                lambda e2: Err([*e1, *e2]),
                lambda _: Err(e1),
            ),
            lambda a: r2.fold(
                lambda e2: Err(e2),
                lambda b: Ok((a, b)),
            ),
        )

    @staticmethod
    def zip3[E, A, B, C](
        r1: Result[Sequence[E], A],
        r2: Result[Sequence[E], B],
        r3: Result[Sequence[E], C],
    ) -> Result[Sequence[E], tuple[A, B, C]]:
        return ResultCombinators.zip(ResultCombinators.zip(r1, r2), r3).map(
            lambda t: (t[0][0], t[0][1], t[1])
        )

    @staticmethod
    def zip4[E, A, B, C, D](
        r1: Result[Sequence[E], A],
        r2: Result[Sequence[E], B],
        r3: Result[Sequence[E], C],
        r4: Result[Sequence[E], D],
    ) -> Result[Sequence[E], tuple[A, B, C, D]]:
        return ResultCombinators.zip(ResultCombinators.zip3(r1, r2, r3), r4).map(
            lambda t: (t[0][0], t[0][1], t[0][2], t[1])
        )

    @staticmethod
    def zip5[E, A, B, C, D, F](
        r1: Result[Sequence[E], A],
        r2: Result[Sequence[E], B],
        r3: Result[Sequence[E], C],
        r4: Result[Sequence[E], D],
        r5: Result[Sequence[E], F],
    ) -> Result[Sequence[E], tuple[A, B, C, D, F]]:
        return ResultCombinators.zip(ResultCombinators.zip4(r1, r2, r3, r4), r5).map(
            lambda t: (t[0][0], t[0][1], t[0][2], t[0][3], t[1])
        )

    @staticmethod
    def map2[E, A, B, C](
        f: Callable[[A, B], C],
        r1: Result[Sequence[E], A],
        r2: Result[Sequence[E], B],
    ) -> Result[Sequence[E], C]:
        return ResultCombinators.zip(r1, r2).map(lambda t: f(t[0], t[1]))

    @staticmethod
    def map3[E, A, B, C, D](
        f: Callable[[A, B, C], D],
        r1: Result[Sequence[E], A],
        r2: Result[Sequence[E], B],
        r3: Result[Sequence[E], C],
    ) -> Result[Sequence[E], D]:
        return ResultCombinators.zip3(r1, r2, r3).map(lambda t: f(t[0], t[1], t[2]))

    @staticmethod
    def map4[E, A, B, C, D, G](
        f: Callable[[A, B, C, D], G],
        r1: Result[Sequence[E], A],
        r2: Result[Sequence[E], B],
        r3: Result[Sequence[E], C],
        r4: Result[Sequence[E], D],
    ) -> Result[Sequence[E], G]:
        return ResultCombinators.zip4(r1, r2, r3, r4).map(lambda t: f(t[0], t[1], t[2], t[3]))

    @staticmethod
    def map5[E, A, B, C, D, F, G](
        f: Callable[[A, B, C, D, F], G],
        r1: Result[Sequence[E], A],
        r2: Result[Sequence[E], B],
        r3: Result[Sequence[E], C],
        r4: Result[Sequence[E], D],
        r5: Result[Sequence[E], F],
    ) -> Result[Sequence[E], G]:
        return ResultCombinators.zip5(r1, r2, r3, r4, r5).map(
            lambda t: f(t[0], t[1], t[2], t[3], t[4])
        )

    @staticmethod
    def combine2[E, A, B](
        r1: Result[Sequence[E], A],
        r2: Result[Sequence[E], B],
    ) -> CombineUsing2[E, A, B]:
        return CombineUsing2(r1, r2)

    @staticmethod
    def combine3[E, A, B, C](
        r1: Result[Sequence[E], A],
        r2: Result[Sequence[E], B],
        r3: Result[Sequence[E], C],
    ) -> CombineUsing3[E, A, B, C]:
        return CombineUsing3(r1, r2, r3)

    @staticmethod
    def combine4[E, A, B, C, D](
        r1: Result[Sequence[E], A],
        r2: Result[Sequence[E], B],
        r3: Result[Sequence[E], C],
        r4: Result[Sequence[E], D],
    ) -> CombineUsing4[E, A, B, C, D]:
        return CombineUsing4(r1, r2, r3, r4)

    @staticmethod
    def combine5[E, A, B, C, D, F](
        r1: Result[Sequence[E], A],
        r2: Result[Sequence[E], B],
        r3: Result[Sequence[E], C],
        r4: Result[Sequence[E], D],
        r5: Result[Sequence[E], F],
    ) -> CombineUsing5[E, A, B, C, D, F]:
        return CombineUsing5(r1, r2, r3, r4, r5)
