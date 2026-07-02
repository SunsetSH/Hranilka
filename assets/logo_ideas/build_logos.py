"""Генерирует 6 концептов логотипа 'Хранилка': початок кукурузы + метафора безопасности.
Пишет SVG-исходники и общий contact-sheet PNG для оценки.
Запуск: venv/Scripts/python.exe assets/logo_ideas/build_logos.py
"""
import os
from PySide6.QtCore import Qt, QByteArray, QRectF
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor, QFont
from PySide6.QtSvg import QSvgRenderer

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- рисунок початка
def corn(cx, cy, w, h, pid, silk=True):
    """SVG-группа: початок кукурузы (сетка зёрен + листья-обёртка)."""
    parts = []
    # тело-капсула для клипа зёрен
    body = (f'M{cx-w},{cy} a{w},{w} 0 0 1 {2*w},0 '
            f'L{cx+w},{cy+h-w} a{w},{w} 0 0 1 {-2*w},0 Z')
    parts.append(f'<clipPath id="cc{pid}"><path d="{body}"/></clipPath>')
    # листья снизу
    lw = w * 1.35
    parts.append(
        f'<path d="M{cx},{cy+h-w*0.4} C{cx-lw},{cy+h*0.7} {cx-lw*0.8},{cy+h*1.15} '
        f'{cx-w*0.15},{cy+h*1.3} C{cx-w*0.4},{cy+h*0.9} {cx-w*0.35},{cy+h*0.7} '
        f'{cx},{cy+h-w*0.4} Z" fill="url(#leaf{pid})"/>')
    parts.append(
        f'<path d="M{cx},{cy+h-w*0.4} C{cx+lw},{cy+h*0.7} {cx+lw*0.8},{cy+h*1.15} '
        f'{cx+w*0.15},{cy+h*1.3} C{cx+w*0.4},{cy+h*0.9} {cx+w*0.35},{cy+h*0.7} '
        f'{cx},{cy+h-w*0.4} Z" fill="url(#leaf{pid})"/>')
    # тело початка (подложка под зёрна)
    parts.append(f'<path d="{body}" fill="#f6c945"/>')
    # зёрна
    cols = 6
    kw = (2 * w) / cols
    kh = kw * 0.82
    parts.append(f'<g clip-path="url(#cc{pid})">')
    y = cy - w * 0.7
    row = 0
    while y < cy + h + kh:
        off = (kw / 2) if row % 2 else 0
        x = cx - w - kw / 2 + off
        while x < cx + w + kw:
            shade = ['#ffdf5e', '#ffd23f', '#f7b733'][(row + int(x)) % 3]
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{kw*0.94:.1f}" '
                f'height="{kh*0.94:.1f}" rx="{kh*0.42:.1f}" fill="{shade}" '
                f'stroke="#e0a419" stroke-width="1.2"/>')
            x += kw
        y += kh * 0.9
        row += 1
    parts.append('</g>')
    # глянец на початке
    parts.append(f'<ellipse cx="{cx-w*0.35}" cy="{cy+h*0.25}" rx="{w*0.35}" '
                 f'ry="{h*0.45}" fill="#ffffff" opacity="0.18"/>')
    # обводка тела
    parts.append(f'<path d="{body}" fill="none" stroke="#c98a12" stroke-width="3"/>')
    if silk:  # рыльца сверху
        parts.append(f'<path d="M{cx-6},{cy-w*0.9} q{-14},-30 {4},-46 M{cx+6},{cy-w*0.9} '
                     f'q{14},-30 {-2},-48 M{cx},{cy-w} l0,-42" '
                     f'stroke="#e7cf7a" stroke-width="4" fill="none" stroke-linecap="round"/>')
    return '\n'.join(parts)


DEFS_LEAF = lambda pid: (
    f'<linearGradient id="leaf{pid}" x1="0" y1="0" x2="0" y2="1">'
    f'<stop offset="0" stop-color="#7ec850"/><stop offset="1" stop-color="#3f8f2e"/>'
    f'</linearGradient>')

# общие defs для бейджей
COMMON = '''
<linearGradient id="blue" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#4aa3ff"/><stop offset="0.5" stop-color="#1f6fe0"/>
 <stop offset="1" stop-color="#0b3f9c"/></linearGradient>
<linearGradient id="gloss" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#ffffff" stop-opacity="0.75"/>
 <stop offset="1" stop-color="#ffffff" stop-opacity="0"/></linearGradient>
<linearGradient id="metal" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#ffffff"/><stop offset="0.5" stop-color="#dbe4ee"/>
 <stop offset="1" stop-color="#9fb0c3"/></linearGradient>
<linearGradient id="cream" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#fff8e6"/><stop offset="1" stop-color="#f3e2b0"/></linearGradient>
<radialGradient id="hole" cx="0.5" cy="0.4" r="0.7">
 <stop offset="0" stop-color="#2a3340"/><stop offset="1" stop-color="#0d1218"/></radialGradient>
<filter id="ds" x="-25%" y="-25%" width="150%" height="150%">
 <feDropShadow dx="0" dy="7" stdDeviation="9" flood-color="#00133a" flood-opacity="0.4"/></filter>
'''

BADGE = ('<rect x="40" y="40" width="432" height="432" rx="104" fill="url(#blue)" filter="url(#ds)"/>'
         '<rect x="40" y="40" width="432" height="432" rx="104" fill="none" '
         'stroke="#ffffff" stroke-opacity="0.25" stroke-width="4"/>'
         '<path d="M64 144 Q64 64 144 64 L368 64 Q448 64 448 144 L448 232 '
         'Q256 296 64 232 Z" fill="url(#gloss)"/>')

SHIELD = ('<path d="M256 96 L392 144 L392 292 Q392 388 256 444 '
          'Q120 388 120 292 L120 144 Z" fill="url(#{f})" stroke="{s}" stroke-width="5"/>')


def svg(inner, pid):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" '
            f'viewBox="0 0 512 512"><defs>{COMMON}{DEFS_LEAF(pid)}</defs>{inner}</svg>')


# ------------------------------------------------------------------- 6 концептов
def v1_shield():  # початок на глянцевом сине-серебряном щите
    inner = BADGE + SHIELD.format(f='metal', s='#8ea3ba') + corn(256, 150, 62, 210, 1)
    return '1_shield', svg(inner, 1)


def v2_padlock():  # початок = корпус замка, металлическая дужка
    shackle = ('<path d="M188 210 L188 150 A68 68 0 0 1 324 150 L324 210" '
               'fill="none" stroke="url(#metal)" stroke-width="34" stroke-linecap="round"/>'
               '<path d="M188 210 L188 150 A68 68 0 0 1 324 150 L324 210" '
               'fill="none" stroke="#7f93a8" stroke-width="34" stroke-linecap="round" opacity="0.25"/>')
    inner = BADGE + shackle + corn(256, 190, 78, 210, 2, silk=False)
    return '2_padlock', svg(inner, 2)


def v3_keyhole():  # початок с вырезанной замочной скважиной = хранит секреты
    kh = ('<circle cx="256" cy="230" r="34" fill="url(#hole)"/>'
          '<path d="M242 248 L270 248 L280 318 L232 318 Z" fill="url(#hole)"/>')
    inner = BADGE + corn(256, 120, 92, 250, 3, silk=False) + kh
    return '3_keyhole', svg(inner, 3)


def v4_husk():  # листья-обёртка поднимаются вверх щитом = природная защита
    light_badge = (
        '<rect x="40" y="40" width="432" height="432" rx="104" fill="url(#cream)" filter="url(#ds)"/>'
        '<rect x="40" y="40" width="432" height="432" rx="104" fill="none" '
        'stroke="#caa74e" stroke-opacity="0.6" stroke-width="4"/>'
        '<path d="M64 144 Q64 64 144 64 L368 64 Q448 64 448 144 L448 232 '
        'Q256 296 64 232 Z" fill="url(#gloss)"/>')
    wrap = ('<path d="M256 120 C150 150 150 300 256 452 C362 300 362 150 256 120 Z" '
            'fill="url(#leaf4)" opacity="0.28"/>'
            '<path d="M256 150 C176 172 176 300 256 420 M256 150 C336 172 336 300 256 420" '
            'stroke="#3f8f2e" stroke-width="8" fill="none" opacity="0.5"/>')
    inner = light_badge + wrap + corn(256, 150, 66, 220, 4)
    return '4_husk', svg(inner, 4)


def v5_vault():  # початок перед круглой дверью сейфа с диском
    import math
    bolts = ''.join(
        f'<circle cx="{256+128*math.cos(math.radians(d)):.0f}" '
        f'cy="{270+128*math.sin(math.radians(d)):.0f}" r="8" fill="#8ea3ba"/>'
        for d in range(0, 360, 30))
    inner = BADGE + '<circle cx="256" cy="270" r="150" fill="url(#metal)" ' \
        'stroke="#8ea3ba" stroke-width="6"/>' + bolts + corn(256, 178, 60, 175, 5)
    return '5_vault', svg(inner, 5)


def v6_emblem():  # початок на кремовом гербовом щите с металлической рамкой (герб/печать)
    frame = (SHIELD.format(f='blue', s='#0b3f9c') +
             '<path d="M256 118 L376 160 L376 290 Q376 380 256 430 '
             'Q136 380 136 290 L136 160 Z" fill="url(#cream)" stroke="#caa74e" stroke-width="4"/>')
    inner = BADGE.replace('url(#blue)', 'url(#metal)') + frame + corn(256, 150, 58, 205, 6)
    return '6_emblem', svg(inner, 6)


VARIANTS = [v1_shield, v2_padlock, v3_keyhole, v4_husk, v5_vault, v6_emblem]

# ------------------------------------------------------------------------ рендер
def render(svg_text, size):
    r = QSvgRenderer(QByteArray(svg_text.encode('utf-8')))
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    r.render(p)
    p.end()
    return img


def main():
    QGuiApplication([])
    built = []
    for fn in VARIANTS:
        name, s = fn()
        with open(os.path.join(HERE, f'{name}.svg'), 'w', encoding='utf-8') as f:
            f.write(s)
        render(s, 256).save(os.path.join(HERE, f'{name}.png'))
        built.append((name, s))

    # contact-sheet 3x2 с подписями
    cell, pad, lab = 256, 24, 34
    cols, rows = 3, 2
    W = cols * cell + (cols + 1) * pad
    H = rows * (cell + lab) + (rows + 1) * pad
    sheet = QImage(W, H, QImage.Format_ARGB32)
    sheet.fill(QColor('#20242b'))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    f = QFont('Segoe UI', 13); f.setBold(True); p.setFont(f)
    for i, (name, s) in enumerate(built):
        c, r = i % cols, i // cols
        x = pad + c * (cell + pad)
        y = pad + r * (cell + lab + pad)
        p.drawImage(QRectF(x, y, cell, cell), render(s, cell))
        p.setPen(QColor('#e8e8e8'))
        p.drawText(QRectF(x, y + cell, cell, lab), Qt.AlignCenter, name)
    p.end()
    sheet.save(os.path.join(HERE, 'contact_sheet.png'))
    print('built:', [n for n, _ in built], '+ contact_sheet.png')


if __name__ == '__main__':
    main()
