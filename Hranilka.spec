# -*- mode: python ; coding: utf-8 -*-
import sys

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable,
    VarFileInfo, VarStruct, VSVersionInfo)

# ЕДИНЫЙ ИСТОЧНИК ВЕРСИИ — hranilka/__init__.py (__version__). Раньше версия
# дублировалась в четырёх строках version_info.txt; теперь метаданные exe
# собираются здесь из одной константы. hranilka/__init__.py содержит только
# докстринг и __version__ — импорт на сборке ничего тяжёлого не тянет.
sys.path.insert(0, SPECPATH)
from hranilka import __version__ as APP_VERSION

# Windows требует ровно 4 числа: "1.1" → (1, 1, 0, 0).
_VER4 = tuple((list(map(int, APP_VERSION.split("."))) + [0, 0, 0])[:4])
_VER_STR = ".".join(map(str, _VER4))

_VERSION_INFO = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=_VER4,
        prodvers=_VER4,
        mask=0x3F,
        flags=0x0,
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo([
            StringTable('041904b0', [
                StringStruct('CompanyName', 'Alexander Kondratyev'),
                StringStruct('FileDescription',
                             'ХРАНИЛКА — локальный хаб учётных записей'),
                StringStruct('FileVersion', _VER_STR),
                StringStruct('InternalName', 'Hranilka'),
                StringStruct('OriginalFilename', 'Hranilka.exe'),
                StringStruct('ProductName', 'ХРАНИЛКА'),
                StringStruct('ProductVersion', _VER_STR),
                StringStruct('LegalCopyright', '© 2026 Alexander Kondratyev'),
            ])
        ]),
        VarFileInfo([VarStruct('Translation', [0x0419, 1200])]),
    ],
)

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
    version=_VERSION_INFO,
)
