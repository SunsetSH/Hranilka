"""Просмотр и экспорт изображений галереи (диалог увеличенного просмотра,
сохранение в файл, копирование в буфер). Функции не знают о GalleryWidget:
сообщения пользователю идут через переданный warn(title, text)."""
from PySide6.QtWidgets import (QLabel, QVBoxLayout, QDialog, QFileDialog,
                               QApplication)
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QImage

from hranilka.ui.widgets.gallery_pipeline import decode_image, guess_ext


def show_full_image(config, parent, image_bytes):
    """Модальный просмотр изображения (до 800x600, декод с bound=1600)."""
    img = decode_image(image_bytes, bound=1600)
    label = QLabel()
    if img is not None:
        label.setPixmap(QPixmap.fromImage(img).scaled(
            800, 600, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    else:
        label.setText("Не удалось отобразить изображение\n"
                      "(повреждено или превышает лимит памяти).")
        label.setAlignment(Qt.AlignCenter)
    label.setAccessibleName("Изображение")
    # Единый стиль программы; откат на обычный QDialog, если config недоступен.
    if config is not None:
        from hranilka.ui.theme import ThemedDialog
        dialog = ThemedDialog(config, parent)
        dialog.setWindowTitle("Просмотр изображения")
        dialog.body.addWidget(label)
    else:
        dialog = QDialog(parent)
        dialog.setWindowTitle("Просмотр изображения")
        QVBoxLayout(dialog).addWidget(label)
    dialog.exec()


def export_image_to_file(parent, image_bytes, warn):
    ext = guess_ext(image_bytes)
    path, _ = QFileDialog.getSaveFileName(
        parent, "Сохранить изображение", "image" + ext,
        f"Изображение (*{ext});;Все файлы (*)")
    if not path:
        return
    try:
        with open(path, "wb") as f:
            f.write(image_bytes)
    except OSError as e:
        warn("Ошибка", f"Не удалось сохранить файл:\n{e}")
        return
    warn("Готово", f"Изображение сохранено:\n{path}")


def export_image_to_clipboard(image_bytes, warn):
    img = QImage()
    if not img.loadFromData(image_bytes):
        warn("Ошибка", "Не удалось прочитать изображение.")
        return
    QApplication.clipboard().setImage(img)
    warn("Готово", "Изображение скопировано в буфер обмена.")
