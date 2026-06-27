import os
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime


_GLOB = "hranilka_backup_*.db"


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
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
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
    """Похож ли файл на пригодную базу Хранилки: зашифрованный контейнер (по
    сигнатуре) ИЛИ нормальный SQLite, проходящий быструю проверку целостности.

    Пароль контейнера здесь не проверяется (его нет) — только структура файла,
    чтобы не подменять рабочую БД заведомо мусором."""
    import crypto_store
    if crypto_store.is_encrypted_file(str(path)):
        return True
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
            return bool(row) and row[0] == "ok"
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
      4) при любой ошибке рабочая БД восстанавливается из rollback-копии."""
    src = Path(backup_path)
    dst = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"Файл бэкапа не найден: {backup_path}")
    if not _is_valid_db(src):
        raise ValueError("Файл бэкапа повреждён или не является базой Хранилки.")

    tmp = dst.with_name(dst.name + ".restore-tmp")
    rollback = dst.with_name(dst.name + ".rollback")
    try:
        shutil.copy2(src, tmp)              # стейджим проверенный кандидат рядом
        if dst.exists():
            shutil.copy2(dst, rollback)     # страховочная копия текущей БД
        try:
            os.replace(tmp, dst)            # атомарная подмена
        except OSError:
            if rollback.exists():
                shutil.copy2(rollback, dst)  # откат к прежнему состоянию
            raise
    finally:
        for p in (tmp, rollback):
            if p.exists():
                p.unlink(missing_ok=True)


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
