"""Исключение слоя данных: FutureSchemaError (файл-на-класс)."""


class FutureSchemaError(Exception):
    """База создана более новой версией программы (её схема новее поддерживаемой).
    Открывать такую базу нельзя: «миграция вниз» повредила бы данные."""

    def __init__(self, found, supported):
        self.found = found
        self.supported = supported
        super().__init__(
            f"База создана более новой версией Хранилки (схема {found}, "
            f"поддерживается {supported}). Обновите программу."
        )
