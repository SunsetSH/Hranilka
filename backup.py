import shutil
from pathlib import Path
from datetime import datetime


_GLOB = "hranilka_backup_*.db"


def create_backup(db_path: str, backup_folder: str, keep_count: int = 5) -> Path:
    """Копирует БД в папку бэкапов с отметкой времени, ротирует старые."""
    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"База данных не найдена: {db_path}")
    folder = Path(backup_folder)
    folder.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = folder / f"hranilka_backup_{ts}.db"
    shutil.copy2(src, dest)
    _rotate(folder, keep_count)
    return dest


def list_backups(backup_folder: str) -> list:
    """Возвращает список файлов бэкапов (новые сначала)."""
    folder = Path(backup_folder)
    if not folder.exists():
        return []
    return sorted(folder.glob(_GLOB), reverse=True)


def restore_backup(backup_path: str, db_path: str) -> None:
    """Заменяет текущую базу выбранным бэкапом."""
    shutil.copy2(backup_path, db_path)


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
