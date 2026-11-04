"""Smoke-тест ядра: зашифрованное хранилище создаётся, переоткрывается
мастер-паролем (Argon2id) и отдаёт дерево.

Запуск (из корня проекта):
    venv/Scripts/python.exe scripts/smoke_test.py

Работает во временном каталоге (удаляется в finally), рабочую базу не трогает.
Код возврата: 0 — успех, 1 — сбой. Qt не нужен.

Проверка frozen-сборки: ssl/_hashlib исключены в Hranilka.spec, поэтому
хэш/KDF-путь стоит проверять на готовом exe. Точка входа exe (main.py) режима
--smoke-test пока не имеет; до его появления скрипт проверяет тот же код на
dev-Python, а frozen-сборку — ручным unlock в окне программы.
"""
from __future__ import annotations

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

PASSWORD = "smoke-test-password"
PRESET = "fast"      # быстрый Argon2: проверяем путь, а не стойкость


def _tree_names(tree: object) -> list[str]:
    """Имена верхнего уровня дерева независимо от точной формы структуры."""
    names: list[str] = []
    if isinstance(tree, dict):
        for value in tree.values():
            names.extend(_tree_names(value))
    elif isinstance(tree, (list, tuple)):
        for item in tree:
            if isinstance(item, dict) and "name" in item:
                names.append(str(item["name"]))
            else:
                names.extend(_tree_names(item))
    return names


def run() -> None:
    from hranilka.crypto import store as cs
    from hranilka.data.database import Database

    with tempfile.TemporaryDirectory(prefix="hranilka-smoke-") as tmp:
        path = os.path.join(tmp, "smoke.db")

        db = Database(path)
        try:
            db.connect()
            db.create_tables()
            service_id = db.add_service("SmokeService")
            db.add_account(service_id, "SmokeAccount")
            db.enable_encryption(PASSWORD, PRESET)
        finally:
            db.close()
            db.shutdown_executor()

        if not cs.is_encrypted_file(path):
            raise AssertionError("файл не зашифрован")

        with open(path, "rb") as f:
            container = f.read()
        try:
            cs.unlock(container, PASSWORD + "x")
        except Exception:
            pass
        else:
            raise AssertionError("неверный пароль принят")

        db_bytes, dek, header = cs.unlock(container, PASSWORD)
        db2 = Database(path)
        try:
            db2.open_encrypted(db_bytes, dek, header)
            db2.create_tables()
            names = _tree_names(db2.get_tree_structure())
        finally:
            db2.close(persist=False)
            db2.shutdown_executor()

        if "SmokeAccount" not in names and "SmokeService" not in names:
            raise AssertionError(f"дерево не содержит данных: {names!r}")


def main() -> int:
    try:
        run()
    except Exception as exc:    # граница процесса: любой сбой -> код 1
        print(f"SMOKE FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
