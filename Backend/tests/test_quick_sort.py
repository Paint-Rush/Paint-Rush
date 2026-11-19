import importlib
import random

import pytest

from app.algorithms import quick_sort as quick_sort_module
from app.algorithms.quick_sort import quick_sort


def run(arr):
    assert quick_sort(arr, 0, len(arr) - 1) is None
    return arr


def test_sorts_in_place():
    data = [5, 3, 8, 4, 9, 1, 6, 2, 7]
    assert run(data) == [1, 2, 3, 4, 5, 6, 7, 8, 9]


@pytest.mark.parametrize("data", [[], [1]])
def test_empty_and_single(data):
    assert run(list(data)) == data


def test_duplicates():
    assert run([3, 1, 3, 2, 1, 3]) == [1, 1, 2, 3, 3, 3]


def test_tuples():
    data = [(-5, 0, 2), (-5, -1, 0), (-9, 0, 1), (-5, 0, 3)]
    assert run(data) == sorted(data)


def test_matches_sorted():
    rng = random.Random(0)
    for _ in range(50):
        data = [rng.randint(-5, 5) for _ in range(rng.randint(0, 20))]
        assert run(list(data)) == sorted(data)


def test_import_has_no_side_effects(capsys):
    importlib.reload(quick_sort_module)
    assert capsys.readouterr().out == ""
    assert not hasattr(quick_sort_module, "data")
