"""Конкурентный доступ к БД: единый RLock + однопоточный executor + поколения
сессий (run_async / StaleSessionError). Часть класса Database (database.py) —
методы вынесены дословно, этап 2 реструктуризации."""
import asyncio
import logging

from hranilka.data.errors import StaleSessionError
from hranilka.data.database.state import DbBase


class DbConcurrencyMixin(DbBase):
    def _bump_session(self):
        """Отметить смену соединения — погасить фоновые операции прежней сессии."""
        self._session_gen += 1
        # Смена соединения (connect/open_encrypted/lock/close/restore) означает
        # другую БД в памяти — кэш объёма галереи больше не актуален (M-9).
        self._gallery_bytes = None

    def current_session(self):
        """Токен текущей сессии БД (растёт при connect/open_encrypted/lock/close).

        Логическая операция из НЕСКОЛЬКИХ await (загрузка/сохранение карточки,
        предпросмотр галереи) должна снять этот токен ОДИН раз в начале и
        передавать его в каждый run_async (_session=…). Тогда смена сессии
        (lock/restore/close) между любыми двумя await прерывает ВСЮ операцию, а не
        только конкретный вызов, стоявший в очереди на момент смены (H65-02)."""
        return self._session_gen

    async def run_async(self, method, *args, _session=None, **kwargs):
        """Выполнить синхронный метод БД в фоновом потоке, не блокируя UI-поток.

        method — публичный метод этого экземпляра (уже обёрнут локом). Результат
        возвращается обычным await. Пример: await db.run_async(db.load_account, id).

        _session — ожидаемый токен сессии (см. current_session). Если не задан,
        снимается текущий на момент вызова (защищает лишь этот вызов). Задавайте
        его явно для многошаговых операций, чтобы вся coroutine была привязана к
        одной сессии.

        Если к моменту выполнения сессия БД сменилась (close/lock/restore) —
        метод НЕ выполняется, поднимается StaleSessionError (вызыватель трактует
        как «результат устарел»)."""
        loop = asyncio.get_running_loop()
        gen = self._session_gen if _session is None else _session

        def _call():
            # Сверка поколения и вызов — под одним локом: close/lock не вклинятся
            # между проверкой и работой метода (метод берёт тот же реентрантный лок).
            with self._lock:
                if self._session_gen != gen:
                    raise StaleSessionError()
                return method(*args, **kwargs)

        return await loop.run_in_executor(self._executor, _call)

    def wait_executor_idle(self, timeout: float = 15.0) -> bool:
        """Дождаться, пока поток-исполнитель run_async опустеет (H7-01).

        Сабмитит в тот же single-worker executor задачу-барьер (no-op) и ждёт её
        завершения: т.к. воркер один и очередь FIFO, к моменту выполнения барьера
        все ранее поставленные run_async-мутаторы уже отработали (и, закоммитив,
        успели пометить БД грязной). Возвращает True, если executor освободился в
        пределах timeout, иначе False (вызыватель ОБЯЗАН прервать свой шаг: не
        закрывать БД, не подменять файл — иначе in-flight-мутатор допишет в уже
        неактуальное соединение). Идемпотентно; при остановленном executor — True.

        Крутить event-loop здесь НЕ нужно: барьер не зависит от UI-потока, а
        Future.result(timeout) блокирует лишь вызывающий (UI) поток на время
        ожидания — это допустимо в привилегированных точках (restore/close)."""
        import concurrent.futures
        ex = getattr(self, "_executor", None)
        if ex is None:
            return True
        try:
            fut = ex.submit(lambda: None)
        except RuntimeError:
            # Executor уже останавливается — задач в нём не осталось.
            return True
        try:
            fut.result(timeout=timeout)
            return True
        except concurrent.futures.TimeoutError:
            logging.warning("run_async-исполнитель не освободился за %s c.", timeout)
            return False

    def shutdown_executor(self):
        """Остановить поток-исполнитель async-операций (идемпотентно). Вызывать на
        выходе из приложения, когда фоновых async-операций уже нет."""
        ex = getattr(self, "_executor", None)
        if ex is not None:
            self._executor = None
            ex.shutdown(wait=True)
