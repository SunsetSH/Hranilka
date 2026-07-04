# -*- mode: python ; coding: utf-8 -*-

# Модули, не используемые приложением (оффлайн, без сети):
#  - PIL: тянется опциональным импортом openpyxl (нужен только для картинок
#    в xlsx, которые экспорт не делает);
#  - PySide6.QtNetwork: сеть в коде не импортируется;
#  - ssl/_ssl/_hashlib: стандартный OpenSSL-стек; asyncio и hashlib работают
#    без них (импорты под guard), шифрование БД — на cryptography/argon2
#    со своими встроенными библиотеками.
_EXCLUDES = [
    "PIL",
    "PySide6.QtNetwork",
    "ssl",
    "_ssl",
    "_hashlib",
]

# Бинарники/данные Qt, которые PyInstaller кладёт «на всякий случай».
# opengl32sw — программный OpenGL-фолбэк (~20 МБ), виджетному приложению
# не нужен. Переводы Qt не загружаются (QTranslator не используется).
# Форматы изображений: PNG встроен в Qt6Gui; jpeg/gif/ico/webp оставляем
# для галереи, экзотику убираем. SVG в приложении нет.
def _drop_binary(name: str) -> bool:
    low = name.lower().replace("\\", "/")
    if low.endswith("opengl32sw.dll"):
        return True
    if "/translations/" in low:
        return True
    if "/platforms/" in low and not low.endswith("qwindows.dll"):
        return True  # qdirect2d/qoffscreen/qminimal
    if "qt6svg" in low or "qsvgicon" in low:
        return True
    if "qtuiotouchplugin" in low:
        return True
    for fmt in ("qsvg.dll", "qtiff.dll", "qicns.dll", "qtga.dll", "qwbmp.dll"):
        if low.endswith(fmt):
            return True
    # Остатки QtNetwork, если что-то протянуло их мимо excludes
    if "qt6network" in low or "/tls/" in low or "/networkinformation/" in low:
        return True
    return False


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    # Иконка окна/панели задач: main.py грузит assets/icon_*.png из
    # RESOURCE_DIR (_MEIPASS в onefile-сборке) — без них QIcon пуст и
    # перекрывает иконку, зашитую в exe.
    datas=[(f"assets/icon_{s}.png", "assets")
           for s in (16, 32, 48, 64, 128, 256)],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDES,
    noarchive=False,
    optimize=2,
)

a.binaries = [b for b in a.binaries if not _drop_binary(b[0])]
a.datas = [d for d in a.datas if not _drop_binary(d[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Hranilka',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
    version='version_info.txt',
)
