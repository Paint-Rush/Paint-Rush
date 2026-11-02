import pytest

from app.algorithms.queue import LinkedQueue


def assert_consistent(queue, expected):
    assert list(queue) == expected
    assert len(queue) == len(expected)
    if expected:
        assert queue.head.item == expected[0]
        assert queue.tail.item == expected[-1]
        assert queue.tail.next is None
    else:
        assert queue.head is None
        assert queue.tail is None


def make_queue(*items):
    queue = LinkedQueue()
    for item in items:
        queue.enqueue(item)
    return queue


def test_empty_queue():
    queue = LinkedQueue()

    assert_consistent(queue, [])
    assert queue.peek_many(3) == []


def test_enqueue_and_dequeue():
    queue = LinkedQueue()
    queue.enqueue("a")

    assert_consistent(queue, ["a"])
    assert queue.dequeue() == "a"
    assert_consistent(queue, [])


def test_fifo_order():
    queue = make_queue(3, 1, 2)

    assert [queue.dequeue() for _ in range(3)] == [3, 1, 2]


def test_peek_keeps_data():
    queue = make_queue(1, 2)

    assert queue.peek() == 1
    assert queue.peek() == 1
    assert_consistent(queue, [1, 2])


def test_peek_many():
    queue = make_queue(1, 2, 3)

    assert queue.peek_many(2) == [1, 2]
    assert queue.peek_many(0) == []
    assert queue.peek_many(10) == [1, 2, 3]
    assert_consistent(queue, [1, 2, 3])
    with pytest.raises(ValueError):
        queue.peek_many(-1)


def test_remove_middle():
    queue = make_queue(1, 2, 3)
    queue.remove(2)

    assert_consistent(queue, [1, 3])


def test_remove_head():
    queue = make_queue(1, 2, 3)
    queue.remove(1)

    assert_consistent(queue, [2, 3])


def test_remove_tail():
    queue = make_queue(1, 2, 3)
    queue.remove(3)

    assert_consistent(queue, [1, 2])
    queue.enqueue(4)
    assert_consistent(queue, [1, 2, 4])


def test_remove_last_node_and_enqueue_again():
    queue = make_queue(1)
    queue.remove(1)
    assert_consistent(queue, [])

    queue.enqueue(2)
    assert_consistent(queue, [2])


def test_remove_missing_item():
    queue = make_queue(1, 2)

    with pytest.raises(ValueError):
        queue.remove(99)
    with pytest.raises(ValueError):
        LinkedQueue().remove(1)
    assert_consistent(queue, [1, 2])


def test_empty_queue_errors():
    queue = LinkedQueue()

    with pytest.raises(IndexError):
        queue.dequeue()
    with pytest.raises(IndexError):
        queue.peek()

    queue.enqueue(1)
    queue.dequeue()
    with pytest.raises(IndexError):
        queue.dequeue()


def test_many_items():
    queue = LinkedQueue()
    for i in range(10000):
        queue.enqueue(i)

    assert len(queue) == 10000
    assert [queue.dequeue() for _ in range(10000)] == list(range(10000))
    assert_consistent(queue, [])


def test_iterate_and_clear():
    queue = make_queue("a", "b", "c")

    assert list(queue) == ["a", "b", "c"]
    assert "b" in queue
    queue.clear()
    assert_consistent(queue, [])
    queue.enqueue("d")
    assert_consistent(queue, ["d"])


def test_head_tail_consistency_after_mixed_operations():
    queue = LinkedQueue()
    expected = []
    for i in range(200):
        queue.enqueue(i)
        expected.append(i)
        if i % 3 == 0:
            assert queue.dequeue() == expected.pop(0)
        if i % 5 == 0 and expected:
            queue.remove(expected[-1])
            expected.pop()
        if i % 7 == 0 and len(expected) > 1:
            queue.remove(expected[1])
            expected.pop(1)
        assert_consistent(queue, expected)
