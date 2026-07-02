import os
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime

import crypto_store
from util import best_effort_wipe


_GLOB = "hranilka_backup_*.db"


def _copy_durable(src: Path, dst: Path) -> None:
    """Копирует src→dst и гарантирует, что данные dst дошли до диска (fsync).

    shutil.copy2 не делает fsync: при сбое питания сразу после копирования файл
    мог остаться пустым/обрезанным. Здесь после записи принудительно сбрасываем
    буферы файла (L3-01)."""
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        shutil.copyfileobj(fsrc, fdst)
        fdst.flush()
        os.fsync(fdst.fileno())


def _fsync_dir(folder: Path) -> None:
    """Best-effort fsync каталога: чтобы переименование файла тоже было
    устойчиво к сбою питания. На Windows fsync каталога не поддерживается —
    тогда тихо пропускаем."""
    try:
        fd = os.open(str(folder), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def create_backup(db_path: str, backup_folder: str, keep_count: int = 5) -> Path:
    """Копирует БД в папку бэкапов с отметкой времени, ротирует старые.

    Имя содержит микросекунды (а не только секунды), поэтому два бэкапа подряд
    не сталкиваются. Файл сначала пишется во временный и атомарно переименуется,
    чтобы обрыв копирования не оставил «обрезанный» бэкап. Ротация выполняется
    только после успешной записи нового файла."""
    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"База данных не найдена: {db_path}")
    folder = Path(backup_folder)
    folder.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = folder / f"hranilka_backup_{ts}.db"
    tmp = folder / f".{dest.name}.tmp"
    try:
        _copy_durable(src, tmp)         # копия + fsync содержимого (L3-01)
        os.replace(tmp, dest)
        _fsync_dir(folder)              # устойчивость самого переименования
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    _rotate(folder, keep_count)
    return dest


def list_backups(backup_folder: str) -> list:
    """Возвращает список файлов бэкапов (новые сначала)."""
    folder = Path(backup_folder)
    if not folder.exists():
        return []
    return sorted(folder.glob(_GLOB), reverse=True)


def _is_valid_db(path: Path) -> bool:
    """Похож ли файл на пригодную базу Хранилки (H3-02 — усиленная проверка).

    Зашифрованный контейнер: раньше валидным считался любой файл с сигнатурой
    `HRNKv1` (проходил даже `b"HRNKv1"`). Теперь выполняется ПОЛНЫЙ структурный
    разбор заголовка (crypto_store._parse: параметры KDF только из пресетов,
    точные длины солей/обёрток/шифртекста). Аутентификацию по паролю здесь
    выполнить нельзя (пароля нет) — она происходит при открытии файла.

    Обычный SQLite: помимо `quick_check` проверяем, что это именно база Хранилки
    (есть обязательные таблицы) и что версия схемы не новее поддерживаемой —
    иначе принимали любую корректную SQLite-базу как бэкап приложения."""
    if crypto_store.is_encrypted_file(str(path)):
        try:
            with open(path, "rb") as f:
                crypto_store._parse(f.read())
            return True
        except Exception:
            return False
    try:
        from database import SCHEMA_VERSION
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
            if not row or row[0] != "ok":
                return False
            # Ссылочная целостность: битые внешние ключи не должны пройти в рабочую
            # БД (M65-04). foreign_key_check возвращает по строке на нарушение.
            if con.execute("PRAGMA foreign_key_check").fetchone() is not None:
                return False
            names = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            # Идентификационные таблицы Хранилки. Полный набор актуальной схемы
            # намеренно НЕ требуем: старый (мигрируемый) бэкап мог не иметь части
            # таблиц — они будут созданы миграцией при открытии после restore.
            # Целостность структуры/ссылок уже покрыта quick_check +
            # foreign_key_check выше (M65-04).
            if not {"accounts", "services", "folders"}.issubset(names):
                return False
            if "app_meta" in names:
                vrow = con.execute(
                    "SELECT value FROM app_meta WHERE key='schema_version'"
                ).fetchone()
                if vrow is not None:
                    try:
                        if int(vrow[0]) > SCHEMA_VERSION:
                            return False
                    except (TypeError, ValueError):
                        return False
            return True
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return False


def restore_backup(backup_path: str, db_path: str) -> None:
    """Заменяет текущую базу выбранным бэкапом — проверяемо и с откатом.

    Раньше функция делала прямой copy2 поверх рабочей БД: обрыв копирования или
    повреждённый/чужой файл безвозвратно уничтожал данные. Теперь:
      1) кандидат проверяется (_is_valid_db) ДО любых изменений рабочего файла;
      2) текущая БД сохраняется в rollback-копию;
      3) кандидат копируется во временный файл рядом и атомарно переименуется;
      4) уже ЗАМЕНЁННЫЙ файл повторно проверяется (_is_valid_db) — и только если
         он действительно открывается, rollback-копия удаляется (H3-02). При
         любой ошибке рабочая БД восстанавливается из rollback-копии."""
    src = Path(backup_path)
    dst = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"Файл бэкапа не найден: {backup_path}")
    if not _is_valid_db(src):
        raise ValueError("Файл бэкапа повреждён или не является базой Хранилки.")

    tmp = dst.with_name(dst.name + ".restore-tmp")
    rollback = dst.with_name(dst.name + ".rollback")
    had_dst = dst.exists()
    committed = False
    try:
        _copy_durable(src, tmp)             # стейджим проверенный кандидат рядом
        if had_dst:
            _copy_durable(dst, rollback)    # страховочная копия текущей БД
        try:
            os.replace(tmp, dst)            # атомарная подмена
        except OSError:
            # Откат атомарно (M6-05): прежняя БД — в durable-копии rollback;
            # os.replace в пределах каталога не оставит dst частично записанным,
            # в отличие от прежнего copy2.
            _rollback_or_preserve(rollback, dst, had_dst)
            raise
        # Подтверждаем, что записанный файл открывается. rollback держим до этого
        # момента — если проверка не прошла, откатываемся к прежней БД.
        if not _is_valid_db(dst):
            _rollback_or_preserve(rollback, dst, had_dst)
            raise ValueError(
                "Восстановленный файл не открывается; выполнен откат к прежней базе.")
        committed = True
    finally:
        # tmp удаляем всегда. rollback — ТОЛЬКО при подтверждённом успехе (M65-03):
        # если откат сам не удался, _rollback_or_preserve уже сохранил последнюю
        # целую копию под recovery-именем, а сам rollback здесь не трогаем.
        # Обе — полные копии БД (пароли/BLOB), поэтому затираем перед удалением (H-3).
        if tmp.exists():
            best_effort_wipe(str(tmp))
        if committed and rollback.exists():
            best_effort_wipe(str(rollback))


def _rollback_or_preserve(rollback: Path, dst: Path, had_dst: bool) -> None:
    """Вернуть dst из rollback-копии. Если сам откат (os.replace) не удался —
    сохранить rollback под recovery-именем и бросить ошибку с путём, чтобы
    последняя целая копия прежней БД не была уничтожена cleanup-кодом (M65-03)."""
    if not (had_dst and rollback.exists()):
        return
    try:
        os.replace(rollback, dst)           # успех: rollback потреблён
    except OSError as e:
        recovery = dst.with_name(dst.name + ".recovery")
        try:
            os.replace(rollback, recovery)
            location = str(recovery)
        except OSError:
            location = str(rollback)         # даже переименовать не смогли — как есть
        raise RuntimeError(
            "Не удалось откатить восстановление; текущий файл базы может быть "
            f"повреждён. Последняя целая копия прежней базы сохранена: {location}"
        ) from e


def delete_all_backups(backup_folder: str) -> int:
    """Удаляет все файлы бэкапов в папке. Возвращает число удалённых файлов."""
    count = 0
    for p in list_backups(backup_folder):
        try:
            p.unlink(missing_ok=True)
            count += 1
        except OSError:
            pass
    return count


def _rotate(folder: Path, keep_count: int) -> None:
    if keep_count <= 0:
        return
    backups = sorted(folder.glob(_GLOB))
    while len(backups) > keep_count:
        backups.pop(0).unlink(missing_ok=True)
