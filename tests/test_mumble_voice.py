from types import SimpleNamespace

from motor_test.mumble_voice import MumbleVoice
from motor_test.pcm import FRAME_BYTES
from tests.audio import constant_frame

BOSS = {"session": 1, "name": "Boss"}
GUEST = {"session": 2, "name": "Guest"}


def chunk(pcm):
    return SimpleNamespace(pcm=pcm)


def voice(max_backlog_frames=10):
    log = []
    return MumbleVoice(max_backlog_frames, log.append), log


def test_nobody_talking_gives_no_frames():
    source, _ = voice()
    assert source.take_frames() == []


def test_a_talker_is_heard_one_frame_per_tick():
    source, _ = voice()
    source.on_sound(BOSS, chunk(constant_frame(1) + constant_frame(2)))
    assert [source.take_frames(), source.take_frames(), source.take_frames()] == [
        [constant_frame(1)],
        [constant_frame(2)],
        [],
    ]


def test_talkers_are_kept_apart_so_the_loop_can_mix_them():
    source, _ = voice()
    source.on_sound(BOSS, chunk(constant_frame(1)))
    source.on_sound(GUEST, chunk(constant_frame(2)))
    assert source.take_frames() == [constant_frame(1), constant_frame(2)]


def test_chunks_are_recut_into_whole_frames():
    source, _ = voice()
    data = constant_frame(4) * 2
    source.on_sound(BOSS, chunk(data[: FRAME_BYTES // 2]))
    assert source.take_frames() == []
    source.on_sound(BOSS, chunk(data[FRAME_BYTES // 2 :]))
    assert source.take_frames() == [constant_frame(4)]


def test_backlog_is_dropped_and_logged():
    source, log = voice(max_backlog_frames=2)
    source.on_sound(BOSS, chunk(b"".join(constant_frame(value) for value in range(5))))
    assert log == ["Dropped 60 ms of Boss's voice to keep up"]
    assert [source.take_frames(), source.take_frames()] == [[constant_frame(3)], [constant_frame(4)]]


def test_connection_changes_are_logged():
    source, log = voice()
    source.on_connected()
    source.on_disconnected()
    assert log == ["Connected to Mumble", "Disconnected from Mumble; pymumble retries every 10 s"]


def test_connected_follows_the_connection_callbacks():
    source, _ = voice()
    assert source.connected is False
    source.on_connected()
    assert source.connected is True
    source.on_disconnected()
    assert source.connected is False
