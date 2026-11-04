"""Воспроизведение «системных» звуков (winsound, только Windows).

winsound.SND_MEMORY несовместим с SND_ASYNC, поэтому синхронный PlaySound
выполняется в фоновом потоке (QThreadPool) — UI не блокируется. Байты WAV
кэшируются по (событие, громкость). Любой сбой звука логируется и не
влияет на работу программы."""
import logging
import sys
from typing import Any, Callable, Optional

from PySide6.QtCore import QRunnable, QThreadPool
from PySide6.QtGui import QGuiApplication

from hranilka.core import sounds_synth

logger = logging.getLogger(__name__)

_cache: dict[tuple[str, int], bytes] = {}


def config_key(event: str) -> str:
    return f"sound_on_{event}"


def wav_bytes(event: str, volume: int) -> bytes:
    """Кэшированный WAV события (KeyError для неизвестного события)."""
    key = (event, sounds_synth.clamp_volume(volume))
    data = _cache.get(key)
    if data is None:
        data = sounds_synth.event_wav(event, key[1])
        _cache[key] = data
    return data


def _winsound_play(data: bytes) -> None:
    import winsound
    winsound.PlaySound(data, winsound.SND_MEMORY)     # синхронно, в потоке


# Точка подмены в тестах: принимает байты WAV.
_player: Callable[[bytes], None] = _winsound_play


class _PlayTask(QRunnable):
    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._data = data

    def run(self) -> None:
        try:
            _player(self._data)
        except Exception as e:                        # noqa: BLE001 — звук не критичен
            logger.debug("Не удалось воспроизвести звук: %s", e)


def is_enabled(config: Any, event: str) -> bool:
    """Нужно ли играть событие: общий выкл, громкость > 0, флаг события."""
    if config is None or event not in sounds_synth.EVENT_TONES:
        return False
    if not config.get("sounds_enabled", True):
        return False
    if sounds_synth.clamp_volume(config.get("sound_volume", 40)) <= 0:
        return False
    return bool(config.get(config_key(event), event == "error"))


def play(config: Any, event: str, *, force: bool = False,
         volume: Optional[int] = None) -> bool:
    """Проиграть звук события в фоне. force — игнорировать флаги (кнопка
    «Проверить» в настройках). True — задание отправлено."""
    if sys.platform != "win32" and _player is _winsound_play:
        return False
    if QGuiApplication.platformName() == "offscreen" \
            and _player is _winsound_play:
        return False                                   # тесты не пищат
    if not force and not is_enabled(config, event):
        return False
    if event not in sounds_synth.EVENT_TONES:
        return False
    vol = volume if volume is not None else (
        config.get("sound_volume", 40) if config is not None else 40)
    try:
        data = wav_bytes(event, vol)
    except Exception as e:                            # noqa: BLE001
        logger.warning("Синтез звука %s не удался: %s", event, e)
        return False
    QThreadPool.globalInstance().start(_PlayTask(data))
    return True
