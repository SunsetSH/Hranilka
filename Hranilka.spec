# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Снижение веса: оставляем только нужные категории Qt-плагинов и выбрасываем
# заведомо ненужные (мы НЕ используем QtSql/QtNetwork/QML/Designer). Картинки
# (imageformats, вкл. webp) и платформенные плагины сохраняем обязательно.
_KEEP_QT_PLUGINS = {
    'platforms',              # qwindows — без него GUI не стартует
    'styles',                 # стили виджетов
    'imageformats',           # JPEG/PNG/WEBP/… для галереи
    'iconengines',
    'platforminputcontexts',  # корректный ввод текста/IME
    'generic',
}


def _keep_in_build(dest_name):
    norm = dest_name.replace('\\', '/')
    marker = 'PySide6/plugins/'
    if marker in norm:
        category = norm.split(marker, 1)[1].split('/', 1)[0]
        return category in _KEEP_QT_PLUGINS
    return True


a.datas = [t for t in a.datas if _keep_in_build(t[0])]
a.binaries = [t for t in a.binaries if _keep_in_build(t[0])]

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
)
