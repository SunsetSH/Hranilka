"""3D-початок: объёмный цилиндр со светотенью. Проверка: крупно + внутри банки.
Запуск: venv/Scripts/python.exe assets/logo_ideas_v2/build_v3.py
"""
import os, math
from PySide6.QtCore import Qt, QByteArray, QRectF
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor, QFont
from PySide6.QtSvg import QSvgRenderer

HERE = os.path.dirname(os.path.abspath(__file__))

DEFS = '''
<linearGradient id="teal" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#37c6c0"/><stop offset="0.5" stop-color="#1b8f9a"/>
 <stop offset="1" stop-color="#0c5560"/></linearGradient>
<linearGradient id="gloss" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#ffffff" stop-opacity="0.75"/>
 <stop offset="1" stop-color="#ffffff" stop-opacity="0"/></linearGradient>
<linearGradient id="metalV" x1="0" y1="0" x2="1" y2="0">
 <stop offset="0" stop-color="#8ea0b3"/><stop offset="0.5" stop-color="#f2f6fa"/>
 <stop offset="1" stop-color="#8ea0b3"/></linearGradient>
<linearGradient id="glass" x1="0" y1="0" x2="1" y2="0">
 <stop offset="0" stop-color="#ffffff" stop-opacity="0.55"/>
 <stop offset="0.35" stop-color="#ffffff" stop-opacity="0.05"/>
 <stop offset="0.75" stop-color="#bfe0ff" stop-opacity="0.12"/>
 <stop offset="1" stop-color="#ffffff" stop-opacity="0.4"/></linearGradient>
<linearGradient id="leaf" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#7ec850"/><stop offset="1" stop-color="#2f7a22"/></linearGradient>
<!-- зерно: выпуклость (свет сверху-слева -> тёмный низ-право) -->
<radialGradient id="kern" cx="0.34" cy="0.28" r="0.85">
 <stop offset="0" stop-color="#fff4b0"/>
 <stop offset="0.35" stop-color="#ffd23f"/>
 <stop offset="0.75" stop-color="#e5a41c"/>
 <stop offset="1" stop-color="#a86e0c"/></radialGradient>
<!-- цилиндрическая светотень по горизонтали -->
<linearGradient id="cyl" x1="0" y1="0" x2="1" y2="0">
 <stop offset="0" stop-color="#3a2400" stop-opacity="0.55"/>
 <stop offset="0.13" stop-color="#3a2400" stop-opacity="0"/>
 <stop offset="0.30" stop-color="#ffffff" stop-opacity="0.30"/>
 <stop offset="0.5" stop-color="#ffffff" stop-opacity="0"/>
 <stop offset="0.78" stop-color="#3a2400" stop-opacity="0.30"/>
 <stop offset="1" stop-color="#3a2400" stop-opacity="0.62"/></linearGradient>
<!-- вертикаль: блик у макушки, тень у кончика -->
<linearGradient id="cylV" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#ffffff" stop-opacity="0.28"/>
 <stop offset="0.16" stop-color="#ffffff" stop-opacity="0"/>
 <stop offset="0.82" stop-color="#3a2400" stop-opacity="0"/>
 <stop offset="1" stop-color="#3a2400" stop-opacity="0.45"/></linearGradient>
<filter id="ds" x="-30%" y="-30%" width="160%" height="160%">
 <feDropShadow dx="0" dy="7" stdDeviation="9" flood-color="#00133a" flood-opacity="0.4"/></filter>
<filter id="softsh" x="-40%" y="-40%" width="180%" height="180%">
 <feDropShadow dx="0" dy="4" stdDeviation="4" flood-color="#5a3a00" flood-opacity="0.4"/></filter>
'''

def corn3d(cx, cy, w, h, uid, leaves=True, silk=True):
    """Объёмный початок. Зёрна на цилиндре: сжатие и затемнение к краям."""
    p = [f'<g filter="url(#softsh)">']
    topY = cy - w * 0.78          # верх зёрен (макушка выше на w)
    botY = cy + h                 # кончик
    # ---- контур тела (капсула с заострённым кончиком)
    body = (f'M{cx-w},{cy} a{w},{w} 0 0 1 {2*w},0 '
            f'C{cx+w},{cy+h*0.72} {cx+w*0.45},{botY} {cx},{botY} '
            f'C{cx-w*0.45},{botY} {cx-w},{cy+h*0.72} {cx-w},{cy} Z')
    bid = f'body{uid}'
    p.append(f'<clipPath id="{bid}"><path d="{body}"/></clipPath>')
    # листья снизу (объёмные, с центральной жилкой)
    if leaves:
        lw = w * 1.35
        for s in (-1, 1):
            p.append(
                f'<path d="M{cx},{cy+h*0.55} C{cx+s*lw},{cy+h*0.82} '
                f'{cx+s*lw*0.75},{cy+h*1.2} {cx+s*w*0.12},{cy+h*1.34} '
                f'C{cx+s*w*0.42},{cy+h*0.98} {cx+s*w*0.34},{cy+h*0.78} '
                f'{cx},{cy+h*0.55} Z" fill="url(#leaf)" stroke="#2f7a22" stroke-width="2"/>')
            p.append(f'<path d="M{cx+s*4},{cy+h*0.6} Q{cx+s*w*0.55},{cy+h*0.95} '
                     f'{cx+s*w*0.14},{cy+h*1.28}" stroke="#2f7a22" stroke-width="2.5" '
                     f'fill="none" opacity="0.6"/>')
    # подложка тела
    p.append(f'<path d="{body}" fill="#e8a91c"/>')
    # ---- зёрна на цилиндрической проекции
    p.append(f'<g clip-path="url(#{bid})">')
    A = 1.22                       #半 угол видимой дуги (рад)
    ncols = 9
    dstep = 2 * A / (ncols - 1)
    nrows = 15
    for i in range(nrows):
        v = i / (nrows - 1)
        y = topY + v * (botY - topY)
        # профиль сужения к кончику
        if v <= 0.7:
            rprof = 1.0
        else:
            rprof = max(0.15, 1 - (v - 0.7) / 0.3 * 0.95)
        rh = (botY - topY) / nrows * 1.15 * (0.7 + 0.3 * rprof)
        shift = (dstep / 2) if i % 2 else 0
        for j in range(ncols + 1):
            th = -A + shift + j * dstep
            if abs(th) > A + 0.02:
                continue
            ct = math.cos(th)
            if ct < 0.18:
                continue
            sx = cx + (w * 0.96 * rprof) * math.sin(th)
            kw = (w * 0.42) * ct * rprof
            kh = rh
            ky = y - (1 - ct) * rh * 0.6      # лёгкий изгиб ряда
            p.append(
                f'<rect x="{sx-kw/2:.1f}" y="{ky-kh/2:.1f}" width="{kw:.1f}" '
                f'height="{kh:.1f}" rx="{min(kw,kh)*0.42:.1f}" fill="url(#kern)" '
                f'stroke="#9c6a0a" stroke-width="0.8" stroke-opacity="0.6"/>')
        i += 1
    # цилиндрическая светотень поверх зёрен
    p.append(f'<path d="{body}" fill="url(#cyl)"/>')
    p.append(f'<path d="{body}" fill="url(#cylV)"/>')
    # диагональный блик-стрик
    p.append(f'<ellipse cx="{cx-w*0.32}" cy="{cy+h*0.2}" rx="{w*0.16}" ry="{h*0.34}" '
             f'fill="#ffffff" opacity="0.25" transform="rotate(-12 {cx-w*0.32} {cy+h*0.2})"/>')
    p.append('</g>')
    # обводка силуэта
    p.append(f'<path d="{body}" fill="none" stroke="#8a5c08" stroke-width="2.5" stroke-opacity="0.7"/>')
    if silk:
        p.append(f'<path d="M{cx},{cy-w*0.7} l0,-30 M{cx-6},{cy-w*0.6} q-12,-22 3,-38 '
                 f'M{cx+6},{cy-w*0.6} q12,-22 -2,-40" stroke="#e7cf7a" '
                 f'stroke-width="3.5" fill="none" stroke-linecap="round" opacity="0.9"/>')
    p.append('</g>')
    return '\n'.join(p)

def svg(inner):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" '
            f'viewBox="0 0 512 512"><defs>{DEFS}</defs>{inner}</svg>')

def badge(grad='teal'):
    return (f'<rect x="40" y="40" width="432" height="432" rx="104" fill="url(#{grad})" filter="url(#ds)"/>'
            '<rect x="40" y="40" width="432" height="432" rx="104" fill="none" '
            'stroke="#ffffff" stroke-opacity="0.25" stroke-width="4"/>'
            '<path d="M64 144 Q64 64 144 64 L368 64 Q448 64 448 144 L448 232 '
            'Q256 296 64 232 Z" fill="url(#gloss)"/>')

def view_alone():
    inner = ('<rect x="0" y="0" width="512" height="512" fill="#e9eef3"/>'
             + corn3d(256, 150, 78, 250, 'a'))
    return 'corn3d_alone', svg(inner)

def view_jar():
    lid = ('<rect x="176" y="96" width="160" height="46" rx="12" fill="url(#metalV)" '
           'stroke="#7f93a8" stroke-width="3"/>'
           '<rect x="188" y="104" width="136" height="10" rx="5" fill="#ffffff" opacity="0.5"/>')
    jar_back = '<rect x="164" y="132" width="184" height="292" rx="46" fill="#0a2f6e" opacity="0.35"/>'
    glass = ('<rect x="164" y="132" width="184" height="292" rx="46" fill="url(#glass)" '
             'stroke="#eaf4ff" stroke-opacity="0.55" stroke-width="3"/>'
             '<rect x="182" y="150" width="26" height="250" rx="13" fill="#ffffff" opacity="0.45"/>'
             '<rect x="306" y="168" width="12" height="210" rx="6" fill="#ffffff" opacity="0.22"/>')
    inner = badge('teal') + jar_back + corn3d(256, 168, 58, 210, 'j', leaves=False, silk=False) + glass + lid
    return 'jar3d', svg(inner)

def render(s, size):
    r = QSvgRenderer(QByteArray(s.encode('utf-8')))
    img = QImage(size, size, QImage.Format_ARGB32); img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    r.render(p); p.end()
    return img

def main():
    QGuiApplication([])
    built = [view_alone(), view_jar()]
    for name, s in built:
        open(os.path.join(HERE, f'{name}.svg'), 'w', encoding='utf-8').write(s)
        render(s, 420).save(os.path.join(HERE, f'{name}.png'))
    cell, pad, lab = 420, 24, 34
    W = 2*cell + 3*pad; H = cell + lab + 2*pad
    sheet = QImage(W, H, QImage.Format_ARGB32); sheet.fill(QColor('#20242b'))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    f = QFont('Segoe UI', 14); f.setBold(True); p.setFont(f)
    for i, (name, s) in enumerate(built):
        x = pad + i*(cell+pad); y = pad
        p.drawImage(QRectF(x, y, cell, cell), render(s, cell))
        p.setPen(QColor('#e8e8e8'))
        p.drawText(QRectF(x, y+cell, cell, lab), Qt.AlignCenter, name)
    p.end()
    sheet.save(os.path.join(HERE, 'sheet_v3.png'))
    print('built:', [n for n, _ in built])

if __name__ == '__main__':
    main()
