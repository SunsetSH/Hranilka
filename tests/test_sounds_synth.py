"""Синтез звуков (core/sounds_synth) и ui/sounds без реального воспроизведения."""
import array
import io
import wave

import pytest

from hranilka.core import sounds_synth as ss


def _read(data: bytes):
    with wave.open(io.BytesIO(data)) as w:
        frames = w.readframes(w.getnframes())
        return w, w.getnchannels(), w.getsampwidth(), w.getframerate(), frames


def _samples(frames: bytes) -> array.array:
    a = array.array("h")
    a.frombytes(frames)
    return a


def test_wav_header_and_length():
    data = ss.build_wav((ss.Tone(440, 100, "square"),))
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    _, ch, width, rate, frames = _read(data)
    assert (ch, width, rate) == (1, 2, ss.SAMPLE_RATE)
    assert len(frames) // 2 == ss.SAMPLE_RATE * 100 // 1000


@pytest.mark.parametrize("ms,expected", [(10, 60), (1000, 200), (120, 120)])
def test_tone_duration_clamped(ms, expected):
    s = ss.tone_samples(ss.Tone(500, ms, "triangle"))
    assert len(s) == ss.SAMPLE_RATE * expected // 1000


def test_volume_scales_amplitude():
    peaks = []
    for vol in (0, 25, 50, 100):
        s = ss.tone_samples(ss.Tone(440, 100, "square"), vol)
        peaks.append(max(abs(x) for x in s))
    assert peaks[0] == 0
    assert peaks == sorted(peaks)
    assert peaks[3] == ss._FULL_SCALE
    assert abs(peaks[2] - peaks[3] // 2) <= 1


def test_triangle_not_clipping_and_volume_clamped():
    s = ss.tone_samples(ss.Tone(300, 100, "triangle"), 500)
    assert max(abs(x) for x in s) <= ss._FULL_SCALE
    assert ss.clamp_volume(-5) == 0 and ss.clamp_volume("x") == 0


def test_invalid_tone_raises():
    with pytest.raises(ValueError):
        ss.tone_samples(ss.Tone(440, 100, "saw"))
    with pytest.raises(ValueError):
        ss.tone_samples(ss.Tone(0, 100, "square"))


def test_all_events_build():
    for ev in ss.EVENTS:
        data = ss.event_wav(ev, 80)
        _, _, _, _, frames = _read(data)
        assert len(_samples(frames)) > 0
    with pytest.raises(KeyError):
        ss.event_wav("nope")


# ---- ui/sounds: флаги и вызов плеера -------------------------------------

class _Cfg:
    def __init__(self, **kw):
        self.d = {"sounds_enabled": True, "sound_volume": 70, "sound_on_error": True,
                  **kw}

    def get(self, k, default=None):
        return self.d.get(k, default)


def test_is_enabled_flags(qapp):
    from hranilka.ui import sounds
    assert sounds.is_enabled(_Cfg(), "error")
    assert not sounds.is_enabled(_Cfg(), "save")
    assert sounds.is_enabled(_Cfg(sound_on_save=True), "save")
    assert not sounds.is_enabled(_Cfg(sounds_enabled=False), "error")
    assert not sounds.is_enabled(_Cfg(sound_volume=0), "error")
    assert not sounds.is_enabled(None, "error")
    assert not sounds.is_enabled(_Cfg(), "unknown")


def test_play_uses_player_and_cache(qapp, monkeypatch):
    from PySide6.QtCore import QThreadPool
    from hranilka.ui import sounds
    got = []
    monkeypatch.setattr(sounds, "_player", got.append)
    assert sounds.play(_Cfg(), "error")
    assert not sounds.play(_Cfg(), "save")
    assert sounds.play(_Cfg(), "save", force=True)
    QThreadPool.globalInstance().waitForDone(3000)
    assert len(got) == 2 and got[0][:4] == b"RIFF"
    assert sounds.wav_bytes("error", 70) is sounds.wav_bytes("error", 70)


def test_player_failure_is_swallowed(qapp, monkeypatch):
    from PySide6.QtCore import QThreadPool
    from hranilka.ui import sounds

    def boom(_data):
        raise RuntimeError("no device")
    monkeypatch.setattr(sounds, "_player", boom)
    assert sounds.play(_Cfg(), "error")
    QThreadPool.globalInstance().waitForDone(3000)
