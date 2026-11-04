"""Синтез коротких «системных» звуков в памяти (WAV, 16 бит, моно). Без Qt.

Файлов-ассетов нет: бип — прямоугольная или треугольная волна 60–200 мс,
громкость задаётся масштабированием амплитуды при генерации."""
import array
import io
import sys
import wave
from typing import NamedTuple

SAMPLE_RATE = 22050
MIN_TONE_MS = 60
MAX_TONE_MS = 200
# Пик при громкости 100 — не полный диапазон: прямоугольная волна и так громкая.
_FULL_SCALE = int(32767 * 0.6)
_FADE_MS = 3                       # короткие фронты — без щелчков


class Tone(NamedTuple):
    freq: int          # Гц
    ms: int            # длительность, будет ограничена 60..200
    shape: str         # "square" | "triangle"


# событие -> последовательность бипов
EVENT_TONES: dict[str, tuple[Tone, ...]] = {
    "error": (Tone(330, 120, "square"), Tone(220, 180, "square")),
    "unlock": (Tone(660, 70, "triangle"), Tone(990, 110, "triangle")),
    "lock": (Tone(990, 70, "triangle"), Tone(660, 110, "triangle")),
    "copy": (Tone(1200, 60, "square"),),
    "save": (Tone(880, 70, "triangle"), Tone(1320, 90, "triangle")),
}
EVENTS = tuple(EVENT_TONES)


def clamp_volume(volume: int) -> int:
    try:
        return max(0, min(100, int(volume)))
    except (TypeError, ValueError):
        return 0


def tone_samples(tone: Tone, volume: int = 100,
                 rate: int = SAMPLE_RATE) -> "array.array[int]":
    """Отсчёты одного бипа (int16). Неизвестная форма -> ValueError."""
    if tone.shape not in ("square", "triangle"):
        raise ValueError(f"неизвестная форма волны: {tone.shape!r}")
    if tone.freq <= 0:
        raise ValueError("частота должна быть > 0")
    ms = max(MIN_TONE_MS, min(MAX_TONE_MS, int(tone.ms)))
    n = rate * ms // 1000
    amp = _FULL_SCALE * clamp_volume(volume) // 100
    fade = max(1, rate * _FADE_MS // 1000)
    period = rate / tone.freq
    out = array.array("h")
    for i in range(n):
        phase = (i % period) / period            # 0..1
        if tone.shape == "square":
            v = 1.0 if phase < 0.5 else -1.0
        else:
            v = 4 * phase - 1 if phase < 0.5 else 3 - 4 * phase
        env = min(1.0, i / fade, (n - 1 - i) / fade) if n > 1 else 1.0
        out.append(int(amp * v * env))
    return out


def build_wav(tones: tuple[Tone, ...], volume: int = 100,
              rate: int = SAMPLE_RATE) -> bytes:
    """WAV-файл (с RIFF-заголовком) из последовательности бипов."""
    data = array.array("h")
    for tone in tones:
        data.extend(tone_samples(tone, volume, rate))
    if data.itemsize != 2:                       # pragma: no cover
        raise RuntimeError("array('h') не 16-битный")
    if sys.byteorder == "big":                   # pragma: no cover — WAV little-endian
        data.byteswap()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())
    return buf.getvalue()


def event_wav(event: str, volume: int = 100) -> bytes:
    """WAV для события из EVENTS. Неизвестное событие -> KeyError."""
    return build_wav(EVENT_TONES[event], volume)
