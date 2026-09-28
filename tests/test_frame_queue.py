import threading

import pytest

from motor_test.frame_queue import FrameQueue
from motor_test.pcm import FRAME_BYTES
from tests.audio import constant_frame


def test_empty_queue_has_nothing_to_take():
    assert FrameQueue(max_frames=10).take() is None


def test_whole_frames_come_out_in_order():
    queue = FrameQueue(max_frames=10)
    queue.put(constant_frame(1) + constant_frame(2))
    assert [queue.take(), queue.take(), queue.take()] == [constant_frame(1), constant_frame(2), None]


def test_a_partial_frame_waits_for_the_rest():
    queue = FrameQueue(max_frames=10)
    data = constant_frame(1) + constant_frame(2)
    queue.put(data[: FRAME_BYTES + 700])
    assert queue.take() == constant_frame(1)
    assert queue.take() is None
    queue.put(data[FRAME_BYTES + 700 :])
    assert queue.take() == constant_frame(2)


def test_odd_byte_counts_lose_and_reorder_nothing():
    queue = FrameQueue(max_frames=10)
    data = constant_frame(1) + constant_frame(-2) + constant_frame(3)
    for start in range(0, len(data), 333):
        queue.put(data[start : start + 333])
    assert [queue.take(), queue.take(), queue.take(), queue.take()] == [
        constant_frame(1),
        constant_frame(-2),
        constant_frame(3),
        None,
    ]


def test_backlog_past_the_limit_drops_the_oldest_frames():
    queue = FrameQueue(max_frames=3)
    dropped = queue.put(b"".join(constant_frame(value) for value in range(5)))
    assert dropped == 2
    assert [queue.take(), queue.take(), queue.take(), queue.take()] == [
        constant_frame(2),
        constant_frame(3),
        constant_frame(4),
        None,
    ]


def test_nothing_is_dropped_within_the_limit():
    assert FrameQueue(max_frames=3).put(constant_frame(0) * 3) == 0


def test_concurrent_puts_keep_every_frame():
    queue = FrameQueue(max_frames=10_000)

    def put_frames():
        for _ in range(500):
            queue.put(constant_frame(7))

    threads = [threading.Thread(target=put_frames) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    taken = 0
    while queue.take() is not None:
        taken += 1
    assert taken == 1000


def test_limit_must_be_at_least_one_frame():
    with pytest.raises(ValueError):
        FrameQueue(max_frames=0)
