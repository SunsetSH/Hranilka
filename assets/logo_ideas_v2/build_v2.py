"""Концепты v2: початок кукурузы ВНУТРИ безопасной зоны (переносный смысл).
Стиль 2000s: градиенты, стекло, металл, блики, объём. Никакого flat.
Запуск: venv/Scripts/python.exe assets/logo_ideas_v2/build_v2.py
"""
import os, math
from PySide6.QtCore import Qt, QByteArray, QRectF
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor, QFont
from PySide6.QtSvg import QSvgRenderer

HERE = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------- початок (компактный)
def corn(cx, cy, w, h, leaves=True, silk=True, op=1.0):
    p = [f'<g opacity="{op}">']
    body = (f'M{cx-w},{cy} a{w},{w} 0 0 1 {2*w},0 '
            f'L{cx+w},{cy+h-w} a{w},{w} 0 0 1 {-2*w},0 Z')
    cid = f'cc{int(cx)}{int(cy)}{int(w)}'
    p.append(f'<clipPath id="{cid}"><path d="{body}"/></clipPath>')
    if leaves:
        lw = w * 1.3
        for s in (-1, 1):
            p.append(
                f'<path d="M{cx},{cy+h-w*0.4} C{cx+s*lw},{cy+h*0.72} '
                f'{cx+s*lw*0.8},{cy+h*1.12} {cx+s*w*0.15},{cy+h*1.26} '
                f'C{cx+s*w*0.4},{cy+h*0.9} {cx+s*w*0.35},{cy+h*0.72} '
                f'{cx},{cy+h-w*0.4} Z" fill="url(#leaf)"/>')
    p.append(f'<path d="{body}" fill="#f6c945"/>')
    cols = 6; kw = (2*w)/cols; kh = kw*0.82
    p.append(f'<g clip-path="url(#{cid})">')
    y = cy - w*0.7; row = 0
    while y < cy + h + kh:
        off = (kw/2) if row % 2 else 0
        x = cx - w - kw/2 + off
        while x < cx + w + kw:
            shade = ['#ffe071', '#ffd23f', '#f6b230'][(row+int(x)) % 3]
            p.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{kw*0.94:.1f}" '
                     f'height="{kh*0.94:.1f}" rx="{kh*0.42:.1f}" fill="{shade}" '
                     f'stroke="#dd9f16" stroke-width="1"/>')
            x += kw
        y += kh*0.9; row += 1
    p.append('</g>')
    p.append(f'<ellipse cx="{cx-w*0.32}" cy="{cy+h*0.22}" rx="{w*0.32}" '
             f'ry="{h*0.42}" fill="#ffffff" opacity="0.16"/>')
    p.append(f'<path d="{body}" fill="none" stroke="#c98a12" stroke-width="2.5"/>')
    if silk:
        p.append(f'<path d="M{cx},{cy-w} l0,-34 M{cx-6},{cy-w*0.9} q-12,-24 3,-40 '
                 f'M{cx+6},{cy-w*0.9} q12,-24 -2,-42" stroke="#e7cf7a" '
                 f'stroke-width="3.5" fill="none" stroke-linecap="round"/>')
    p.append('</g>')
    return '\n'.join(p)

# ------------------------------------------------------------------------- defs
DEFS = '''
<linearGradient id="blue" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#4aa3ff"/><stop offset="0.5" stop-color="#1f6fe0"/>
 <stop offset="1" stop-color="#0b3f9c"/></linearGradient>
<linearGradient id="teal" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#37c6c0"/><stop offset="0.5" stop-color="#1b8f9a"/>
 <stop offset="1" stop-color="#0c5560"/></linearGradient>
<linearGradient id="warm" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#ffcf7a"/><stop offset="0.5" stop-color="#e8892f"/>
 <stop offset="1" stop-color="#a2470f"/></linearGradient>
<linearGradient id="gloss" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#ffffff" stop-opacity="0.75"/>
 <stop offset="1" stop-color="#ffffff" stop-opacity="0"/></linearGradient>
<linearGradient id="leaf" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#7ec850"/><stop offset="1" stop-color="#3f8f2e"/></linearGradient>
<linearGradient id="metal" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stop-color="#ffffff"/><stop offset="0.45" stop-color="#cfd9e4"/>
 <stop offset="0.55" stop-color="#aebccd"/><stop offset="1" stop-color="#e8eef4"/></linearGradient>
<linearGradient id="metalV" x1="0" y1="0" x2="1" y2="0">
 <stop offset="0" stop-color="#8ea0b3"/><stop offset="0.5" stop-color="#f2f6fa"/>
 <stop offset="1" stop-color="#8ea0b3"/></linearGradient>
<linearGradient id="glass" x1="0" y1="0" x2="1" y2="0">
 <stop offset="0" stop-color="#ffffff" stop-opacity="0.55"/>
 <stop offset="0.35" stop-color="#ffffff" stop-opacity="0.05"/>
 <stop offset="0.75" stop-color="#bfe0ff" stop-opacity="0.12"/>
 <stop offset="1" stop-color="#ffffff" stop-opacity="0.4"/></linearGradient>
<radialGradient id="amber" cx="0.4" cy="0.35" r="0.75">
 <stop offset="0" stop-color="#ffcf5e"/><stop offset="0.55" stop-color="#e78b1f"/>
 <stop offset="1" stop-color="#9c4a0d"/></radialGradient>
<radialGradient id="gold" cx="0.4" cy="0.35" r="0.8">
 <stop offset="0" stop-color="#fff3b0"/><stop offset="0.5" stop-color="#f2c231"/>
 <stop offset="1" stop-color="#b5820f"/></radialGradient>
<filter id="ds" x="-30%" y="-30%" width="160%" height="160%">
 <feDropShadow dx="0" dy="7" stdDeviation="9" flood-color="#00133a" flood-opacity="0.4"/></filter>
'''

def badge(grad='blue'):
    return (f'<rect x="40" y="40" width="432" height="432" rx="104" fill="url(#{grad})" filter="url(#ds)"/>'
            '<rect x="40" y="40" width="432" height="432" rx="104" fill="none" '
            'stroke="#ffffff" stroke-opacity="0.25" stroke-width="4"/>'
            '<path d="M64 144 Q64 64 144 64 L368 64 Q448 64 448 144 L448 232 '
            'Q256 296 64 232 Z" fill="url(#gloss)"/>')

def svg(inner):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" '
            f'viewBox="0 0 512 512"><defs>{DEFS}</defs>{inner}</svg>')

# --------------------------------------------------------------------- концепты
def c1_jar():  # банка-консервация: початок закатан в стекло, металлическая крышка
    lid = ('<rect x="176" y="96" width="160" height="46" rx="12" fill="url(#metalV)" '
           'stroke="#7f93a8" stroke-width="3"/>'
           '<rect x="188" y="104" width="136" height="10" rx="5" fill="#ffffff" opacity="0.5"/>')
    jar_back = '<rect x="164" y="132" width="184" height="292" rx="46" fill="#0a2f6e" opacity="0.35"/>'
    glass = ('<rect x="164" y="132" width="184" height="292" rx="46" fill="url(#glass)" '
             'stroke="#eaf4ff" stroke-opacity="0.55" stroke-width="3"/>'
             '<rect x="182" y="150" width="26" height="250" rx="13" fill="#ffffff" opacity="0.45"/>'
             '<rect x="306" y="168" width="12" height="210" rx="6" fill="#ffffff" opacity="0.22"/>')
    inner = badge('teal') + jar_back + corn(256, 168, 58, 210, leaves=False, silk=False) + glass + lid
    return 'jar', svg(inner)

def c2_dome():  # стеклянный купол на подставке = под защитой, как экспонат
    base = ('<rect x="150" y="392" width="212" height="34" rx="14" fill="url(#metalV)" '
            'stroke="#7f93a8" stroke-width="3"/>')
    dome_back = '<path d="M150 400 L150 250 A106 106 0 0 1 362 250 L362 400 Z" fill="#0a2f6e" opacity="0.3"/>'
    corn_in = corn(256, 196, 56, 190, leaves=False)
    dome = ('<path d="M150 400 L150 250 A106 106 0 0 1 362 250 L362 400 Z" fill="url(#glass)" '
            'stroke="#eaf4ff" stroke-opacity="0.55" stroke-width="3"/>'
            '<path d="M180 392 L180 258 A76 76 0 0 1 210 205" stroke="#ffffff" '
            'stroke-opacity="0.5" stroke-width="16" fill="none" stroke-linecap="round"/>'
            '<circle cx="256" cy="150" r="14" fill="url(#metal)" stroke="#7f93a8" stroke-width="3"/>')
    inner = badge('blue') + dome_back + corn_in + dome + base
    return 'dome', svg(inner)

def c3_amber():  # застыл в янтаре — сохранён навсегда, тёплое доверие
    blob = ('<path d="M256 92 C356 96 404 190 392 286 C382 372 320 430 256 434 '
            'C192 430 130 372 120 286 C108 190 156 96 256 92 Z" fill="url(#amber)" '
            'stroke="#7a3808" stroke-width="4"/>')
    corn_in = corn(256, 150, 52, 205, leaves=False, silk=False, op=0.92)
    shine = ('<path d="M256 92 C356 96 404 190 392 286 C382 372 320 430 256 434 '
             'C192 430 130 372 120 286 C108 190 156 96 256 92 Z" fill="url(#glass)" opacity="0.7"/>'
             '<ellipse cx="200" cy="180" rx="46" ry="70" fill="#ffffff" opacity="0.35" '
             'transform="rotate(-25 200 180)"/>'
             '<circle cx="330" cy="150" r="12" fill="#ffffff" opacity="0.55"/>')
    inner = badge('warm') + blob + corn_in + shine
    return 'amber', svg(inner)

def c4_capsule():  # герметичная капсула с металлическими торцами
    cap = ('<rect x="150" y="112" width="212" height="80" rx="40" fill="url(#metal)" '
           'stroke="#7f93a8" stroke-width="3"/>'
           '<rect x="150" y="332" width="212" height="80" rx="40" fill="url(#metal)" '
           'stroke="#7f93a8" stroke-width="3"/>'
           '<rect x="168" y="124" width="176" height="12" rx="6" fill="#ffffff" opacity="0.5"/>')
    glass_back = '<rect x="150" y="150" width="212" height="224" fill="#0a2f6e" opacity="0.3"/>'
    corn_in = corn(256, 168, 56, 190, leaves=False, silk=False)
    glass = ('<rect x="150" y="150" width="212" height="224" fill="url(#glass)"/>'
             '<rect x="172" y="150" width="24" height="224" rx="12" fill="#ffffff" opacity="0.4"/>')
    inner = badge('teal') + glass_back + corn_in + glass + cap
    return 'capsule', svg(inner)

def c5_porthole():  # толстая дверь сейфа с круглым стеклянным иллюминатором
    door = ('<rect x="70" y="70" width="372" height="372" rx="60" fill="url(#metal)" '
            'stroke="#7f93a8" stroke-width="4"/>'
            + ''.join(f'<circle cx="{x}" cy="{y}" r="10" fill="#8ea0b3"/>'
                      for x, y in [(112,112),(400,112),(112,400),(400,400)]))
    rim = ('<circle cx="256" cy="248" r="132" fill="#7f93a8"/>'
           '<circle cx="256" cy="248" r="132" fill="none" stroke="url(#metalV)" stroke-width="18"/>'
           '<circle cx="256" cy="248" r="116" fill="#0a2f6e" opacity="0.45"/>')
    corn_in = corn(256, 156, 50, 178, leaves=False)
    glassdisc = ('<clipPath id="pg"><circle cx="256" cy="248" r="116"/></clipPath>'
                 '<g clip-path="url(#pg)"><circle cx="256" cy="248" r="116" fill="url(#glass)"/>'
                 '<ellipse cx="206" cy="196" rx="40" ry="66" fill="#ffffff" opacity="0.4" '
                 'transform="rotate(-20 206 196)"/></g>')
    handle = ('<circle cx="256" cy="248" r="20" fill="url(#metal)" stroke="#7f93a8" stroke-width="3"/>'
              + ''.join(f'<rect x="250" y="200" width="12" height="96" rx="6" fill="url(#metal)" '
                        f'stroke="#7f93a8" stroke-width="2" transform="rotate({a} 256 248)"/>'
                        for a in (0, 90)))
    inner = door + rim + corn_in + glassdisc + handle
    return 'porthole', svg(inner)

def c6_treasure():  # сундук-сейф, из которого золотой початок = сбережения
    chest = ('<path d="M120 220 Q120 180 160 180 L352 180 Q392 180 392 220 L392 250 L120 250 Z" '
             'fill="url(#gold)" stroke="#8a5f0c" stroke-width="4"/>'
             '<rect x="112" y="248" width="288" height="176" rx="26" fill="url(#warm)" '
             'stroke="#8a5f0c" stroke-width="4"/>'
             '<rect x="112" y="300" width="288" height="18" fill="#8a5f0c" opacity="0.5"/>'
             '<rect x="236" y="300" width="40" height="52" rx="8" fill="url(#metal)" '
             'stroke="#7f93a8" stroke-width="3"/>'
             '<circle cx="256" cy="320" r="7" fill="#3a3f46"/>')
    corn_in = corn(256, 150, 52, 150, leaves=False)
    glow = '<ellipse cx="256" cy="200" rx="150" ry="60" fill="#fff3b0" opacity="0.3"/>'
    inner = badge('blue') + glow + corn_in + chest
    return 'treasure', svg(inner)

VARIANTS = [c1_jar, c2_dome, c3_amber, c4_capsule, c5_porthole, c6_treasure]

# -------------------------------------------------------------------------- render
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
    built = []
    for fn in VARIANTS:
        name, s = fn()
        open(os.path.join(HERE, f'{name}.svg'), 'w', encoding='utf-8').write(s)
        render(s, 256).save(os.path.join(HERE, f'{name}.png'))
        built.append((name, s))
    cell, pad, lab, cols, rows = 256, 24, 34, 3, 2
    W = cols*cell + (cols+1)*pad
    H = rows*(cell+lab) + (rows+1)*pad
    sheet = QImage(W, H, QImage.Format_ARGB32); sheet.fill(QColor('#20242b'))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    f = QFont('Segoe UI', 13); f.setBold(True); p.setFont(f)
    for i, (name, s) in enumerate(built):
        c, r = i % cols, i // cols
        x = pad + c*(cell+pad); y = pad + r*(cell+lab+pad)
        p.drawImage(QRectF(x, y, cell, cell), render(s, cell))
        p.setPen(QColor('#e8e8e8'))
        p.drawText(QRectF(x, y+cell, cell, lab), Qt.AlignCenter, name)
    p.end()
    sheet.save(os.path.join(HERE, 'contact_sheet.png'))
    print('built:', [n for n, _ in built])

if __name__ == '__main__':
    main()
