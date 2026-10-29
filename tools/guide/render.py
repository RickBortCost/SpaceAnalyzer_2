"""Generatore del PDF della guida (reportlab) a partire dai file Markdown di tools/guide/content/<lingua>/.

Sottoinsieme di Markdown riconosciuto:
  # Titolo capitolo           capitolo (nuova pagina, voce di primo livello dell'indice)
  ## Titolo sezione           sezione (voce di secondo livello)
  testo con **grassetto**, *corsivo* e `codice`
  - elenco puntato
  | A | B |  tabelle (la seconda riga |---|---| separa l'intestazione)
  > nota in riquadro arancione
  ![id](didascalia)           figura figures/<id>.jpg con la didascalia
  ```                         blocco di codice
Segnaposto: {{version}} (es. 3.13.0), {{minor}} (es. 3.13).
"""
import re
from pathlib import Path

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, Image as RLImage, KeepTogether, PageBreak, PageTemplate, Paragraph, Preformatted, Spacer, Table,
                                TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FIGURES = HERE / 'figures'

ORANGE = colors.HexColor('#E8590C')
DARK = colors.HexColor('#1F2937')
GRAY = colors.HexColor('#6B7280')
LINE = colors.HexColor('#D5DCE4')
HEAD_BG = colors.HexColor('#EEF2F7')
NOTE_BG = colors.HexColor('#FFF1E5')
CODE_BG = colors.HexColor('#F3F4F6')

FONT_DIRS = ['/usr/share/fonts/truetype/dejavu', '/usr/share/fonts/dejavu', 'C:/Windows/Fonts', '/Library/Fonts']


def register_fonts():
    # Se il sistema non ha i caratteri obliqui (DejaVu Sans minimale) si usano quelli diritti: il corsivo resta senza inclinazione.
    names = {'DejaVuSans': ('DejaVuSans.ttf',), 'DejaVuSans-Bold': ('DejaVuSans-Bold.ttf',),
             'DejaVuSans-Oblique': ('DejaVuSans-Oblique.ttf', 'DejaVuSans.ttf'),
             'DejaVuSans-BoldOblique': ('DejaVuSans-BoldOblique.ttf', 'DejaVuSans-Bold.ttf'), 'DejaVuSansMono': ('DejaVuSansMono.ttf',)}
    for name, candidates in names.items():
        for file in candidates:
            path = next((Path(folder) / file for folder in FONT_DIRS if (Path(folder) / file).exists()), None)
            if path:
                pdfmetrics.registerFont(TTFont(name, str(path)))
                break
        else:
            raise RuntimeError(f'Font {candidates[0]} non trovato: installare DejaVu (fonts-dejavu-core)')
    pdfmetrics.registerFontFamily('DejaVuSans', normal='DejaVuSans', bold='DejaVuSans-Bold', italic='DejaVuSans-Oblique', boldItalic='DejaVuSans-BoldOblique')


def styles():
    base = dict(fontName='DejaVuSans', textColor=DARK, alignment=TA_LEFT)
    return {
        'body': ParagraphStyle('body', fontSize=9.6, leading=13.6, spaceAfter=6, **base),
        'bullet': ParagraphStyle('bullet', fontSize=9.6, leading=13.6, spaceAfter=3, leftIndent=14, bulletIndent=3, **base),
        'h1': ParagraphStyle('h1', fontName='DejaVuSans-Bold', fontSize=21, leading=26, textColor=ORANGE, spaceBefore=0, spaceAfter=10, keepWithNext=1),
        'h2': ParagraphStyle('h2', fontName='DejaVuSans-Bold', fontSize=12.5, leading=16, textColor=DARK, spaceBefore=10, spaceAfter=5, keepWithNext=1),
        'caption': ParagraphStyle('caption', fontName='DejaVuSans', fontSize=8, leading=10.5, textColor=GRAY, spaceBefore=1, spaceAfter=7),
        'cell': ParagraphStyle('cell', fontName='DejaVuSans', fontSize=8.3, leading=11, textColor=DARK),
        'cellhead': ParagraphStyle('cellhead', fontName='DejaVuSans-Bold', fontSize=8.3, leading=11, textColor=DARK),
        'note': ParagraphStyle('note', fontName='DejaVuSans', fontSize=8.6, leading=12, textColor=DARK),
        'code': ParagraphStyle('code', fontName='DejaVuSansMono', fontSize=8, leading=10.5, textColor=DARK),
        'title': ParagraphStyle('title', fontName='DejaVuSans-Bold', fontSize=30, leading=36, textColor=ORANGE, spaceAfter=4),
        'subtitle': ParagraphStyle('subtitle', fontName='DejaVuSans', fontSize=14, leading=19, textColor=DARK, spaceAfter=8),
        'meta': ParagraphStyle('meta', fontName='DejaVuSans', fontSize=10, leading=14, textColor=DARK, spaceAfter=10),
        'toc0': ParagraphStyle('toc0', fontName='DejaVuSans-Bold', fontSize=12.5, leading=17, textColor=ORANGE, spaceBefore=12, spaceAfter=3),
        'toc1': ParagraphStyle('toc1', fontName='DejaVuSans', fontSize=9.6, leading=14, leftIndent=18, textColor=colors.black),
        'toc2': ParagraphStyle('toc2', fontName='DejaVuSans', fontSize=8.3, leading=12, leftIndent=34, textColor=colors.HexColor('#4B5563')),
    }


def inline(text, version):
    """Markdown in linea -> mini HTML di reportlab."""
    text = text.replace('{{version}}', version).replace('{{minor}}', '.'.join(version.split('.')[:2]))
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(r'`([^`]+)`', lambda m: '<font name="DejaVuSansMono" size="8.4">' + m.group(1) + '</font>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?<![*\w])\*([^*\n]+)\*(?!\w)', r'<i>\1</i>', text)
    return text


class Heading(Paragraph):
    """Titolo che notifica l'indice (livello 0 = edizione, 1 = capitolo, 2 = sezione)."""

    def __init__(self, text, style, level, key):
        super().__init__(text, style)
        self.level, self.key, self.plain = level, key, re.sub(r'<[^>]+>', '', text)


class EditionMark(Flowable):
    """Segnaposto invisibile che aggiunge l'edizione all'indice."""

    def __init__(self, title, key):
        super().__init__()
        self.title, self.key = title, key
        self.width = self.height = 0

    def draw(self):
        pass


class Doc(BaseDocTemplate):
    def __init__(self, path, version, footer, title, **kw):
        super().__init__(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm, title=title,
                         author='Space Fieldbus Analyzer', subject='Guida illustrata / Illustrated guide', **kw)
        self.version, self.footer = version, footer
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id='f', leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate(id='all', frames=[frame], onPage=self.decorate)])
        self.bookmark_n = 0

    def decorate(self, canvas, doc):
        canvas.saveState()
        canvas.setFont('DejaVuSans', 7.6)
        canvas.setFillColor(GRAY)
        canvas.drawString(self.leftMargin - 4 * mm, 10 * mm, self.footer)
        canvas.drawRightString(A4[0] - self.rightMargin + 4 * mm, 10 * mm, str(doc.page))
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Heading) and flowable.level in (1, 2):
            self.notify('TOCEntry', (flowable.level, flowable.plain, self.page, flowable.key))
            self.canv.bookmarkPage(flowable.key)
            self.canv.addOutlineEntry(flowable.plain, flowable.key, level=flowable.level - 1, closed=flowable.level == 1)
        elif isinstance(flowable, Heading) and flowable.level == 0:
            self.notify('TOCEntry', (0, flowable.plain, self.page, flowable.key))
            self.canv.bookmarkPage(flowable.key)
            self.canv.addOutlineEntry(flowable.plain, flowable.key, level=0, closed=False)


# ---- Markdown -> flowable --------------------------------------------------------------------------------------------------

def parse_table(lines, st, version, width):
    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        rows.append(cells)
    header, body = rows[0], [r for r in rows[2:]]
    n = len(header)
    weights = []
    for c in range(n):
        longest = max([len(header[c])] + [len(re.sub(r'[*`]', '', r[c])) if c < len(r) else 0 for r in body])
        mean = sum(len(re.sub(r'[*`]', '', r[c])) if c < len(r) else 0 for r in body) / max(1, len(body))
        weights.append(max(6.0, min(longest, 28) * 0.55 + min(mean, 120) * 0.45))
    if header[0] in ('N.', 'N', '#'):
        weights[0] = 3.6
    total = sum(weights)
    widths = [max(width * w / total, 12 * mm) for w in weights]
    scale = width / sum(widths)
    widths = [w * scale for w in widths]
    data = [[Paragraph(inline(h, version), st['cellhead']) for h in header]]
    for r in body:
        r = r + [''] * (n - len(r))
        data.append([Paragraph(inline(c, version), st['cell']) for c in r[:n]])
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HEAD_BG), ('GRID', (0, 0), (-1, -1), 0.4, LINE), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5), ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    return table


def figure_flowables(fid, caption, st, version, width):
    path = FIGURES / f'{fid}.jpg'
    if not path.exists():
        raise FileNotFoundError(f'Figura mancante: {path} (eseguire tools/guide/capture.py)')
    w, h = Image.open(path).size
    shown = min(width, w * 0.1 * mm * 10 / 10)   # 0,1 mm per pixel, al massimo tutta la larghezza
    shown = min(width, w * 0.1 * mm)
    scale = shown / w
    height = h * scale
    max_h = 205 * mm
    if height > max_h:
        scale = max_h / h
        shown, height = w * scale, max_h
    img = RLImage(str(path), width=shown, height=height)
    img.hAlign = 'CENTER'
    parts = [img]
    if caption:
        parts.append(Paragraph(inline(caption, version), st['caption']))
    return KeepTogether(parts)


def note_flowable(text, st, version, width):
    p = Paragraph(inline(text, version), st['note'])
    t = Table([[p]], colWidths=[width])
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), NOTE_BG), ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
                           ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
    return [t, Spacer(1, 6)]


def markdown_flowables(text, st, version, width, edition, counters):
    out, lines, i = [], text.split('\n'), 0
    paragraph = []

    def flush():
        if paragraph:
            out.append(Paragraph(inline(' '.join(paragraph), version), st['body']))
            paragraph.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            flush(); i += 1; continue
        if s.startswith('```'):
            flush(); i += 1; block = []
            while i < len(lines) and not lines[i].strip().startswith('```'):
                block.append(lines[i]); i += 1
            i += 1
            t = Table([[Preformatted('\n'.join(block), st['code'])]], colWidths=[width])
            t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), CODE_BG), ('LEFTPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
            out += [t, Spacer(1, 6)]
            continue
        if s.startswith('# '):
            flush(); counters['n'] += 1
            out.append(PageBreak())
            counters['chapters'] += 1
            out.append(Heading(inline(s[2:], version), st['h1'], 1, f'{edition}-c{counters["n"]}'))
            i += 1; continue
        if s.startswith('## '):
            flush(); counters['n'] += 1
            out.append(Heading(inline(s[3:], version), st['h2'], 2, f'{edition}-s{counters["n"]}'))
            i += 1; continue
        if s.startswith('!['):
            flush()
            m = re.match(r'!\[([^\]]+)\]\((.*)\)\s*$', s)
            if not m:
                raise ValueError('Figura non valida: ' + s)
            out.append(figure_flowables(m.group(1), m.group(2), st, version, width))
            i += 1; continue
        if s.startswith('|'):
            flush(); block = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                block.append(lines[i]); i += 1
            out += [parse_table(block, st, version, width), Spacer(1, 7)]
            continue
        if s.startswith('> '):
            flush(); block = []
            while i < len(lines) and lines[i].strip().startswith('>'):
                block.append(lines[i].strip()[1:].strip()); i += 1
            out += note_flowable(' '.join(block), st, version, width)
            continue
        if s.startswith('- '):
            flush(); out.append(Paragraph(inline(s[2:], version), st['bullet'], bulletText='•')); i += 1; continue
        paragraph.append(s); i += 1
    flush()
    return out


def edition_flowables(folder, edition, version, st, width, cover):
    counters = {'n': 0, 'chapters': 0}
    out = []
    for path in sorted(folder.glob('*.md')):
        text = path.read_text(encoding='utf-8')
        if path.name.startswith('00_'):
            continue
        part = markdown_flowables(text, st, version, width, edition, counters)
        out += part
    return out


def build(pdf, version, editions, cover_notes, labels, footer, title):
    """editions: [(chiave, titolo edizione, cartella md, titolo copertina, sottotitolo, riga versione)]."""
    register_fonts()
    st = styles()
    doc = Doc(pdf, version, footer, title)
    width = doc.width
    story = []
    logo = ROOT / 'branding/space-analyzer-logo.png'

    def logo_flowable(size):
        w, h = Image.open(logo).size
        im = RLImage(str(logo), width=size, height=size * h / w)
        im.hAlign = 'LEFT'
        return im

    # copertina generale + indice
    story += [Spacer(1, 28 * mm), logo_flowable(34 * mm), Spacer(1, 14 * mm), Paragraph(f'Space Fieldbus Analyzer {version}', st['title']),
              Paragraph(labels['subtitle'], st['subtitle']), Spacer(1, 6 * mm), Paragraph(labels['intro'], st['meta']),
              Spacer(1, 4 * mm), Paragraph(labels['index'], st['h2'])]
    toc = TableOfContents()
    toc.levelStyles = [st['toc0'], st['toc1'], st['toc2']]
    toc.dotsMinLevel = 0
    story.append(toc)
    for key, ed_title, folder, cover_title, cover_sub, cover_line in editions:
        story.append(PageBreak())
        story.append(Spacer(1, 40 * mm))
        story.append(logo_flowable(38 * mm))
        story.append(Spacer(1, 14 * mm))
        story.append(Heading(inline(ed_title, version), st['title'].clone('edtitle', fontSize=26, leading=32), 0, f'{key}-ed'))
        story.append(Paragraph(inline(cover_sub, version), st['subtitle']))
        story.append(Paragraph(inline(cover_line, version), st['meta']))
        story.append(Spacer(1, 10 * mm))
        notes = (Path(folder) / '00_cover.md')
        if notes.exists():
            story += markdown_flowables(notes.read_text(encoding='utf-8'), st, version, width, key + 'cov', {'n': 0, 'chapters': 1})
        story += edition_flowables(Path(folder), key, version, st, width, None)
    doc.multiBuild(story)
