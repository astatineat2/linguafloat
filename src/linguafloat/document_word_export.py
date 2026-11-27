"""Academic Word styles for both bound manuscripts and offline PDF recovery."""

import io
import re
from itertools import groupby

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


LATIN = 'Times New Roman'
MATH = 'Cambria Math'
MATH_GLYPHS = re.compile(r'([\U0001d400-\U0001d7ff]+)')
STYLES = {
    'Normal': ('SimSun', 12, False),
    'Title': ('SimHei', 16, False),
    'Subtitle': ('SimSun', 14, False),
    'Heading 1': ('SimSun', 14, True),
    'Heading 2': ('SimSun', 12, True),
    'Heading 3': ('SimSun', 12, False),
    'Caption': ('SimSun', 7.5, True),
    'Academic Author': ('FangSong', 10.5, False),
    'Academic Affiliation': ('FangSong', 10.5, False),
    'Academic Abstract': ('SimSun', 14, False),
    'Academic Keywords': ('SimSun', 14, False),
    'Academic Formula': ('SimSun', 12, False),
}
ROLES = dict(main_title='Title', subtitle='Subtitle', author='Academic Author',
             affiliation='Academic Affiliation', abstract='Academic Abstract',
             keywords='Academic Keywords', formula='Academic Formula',
             heading1='Heading 1', heading2='Heading 2', heading3='Heading 3')
HAN = re.compile(r'[\u3400-\u9fff]')
LABEL = re.compile(r'^((?:\d+[.\u3001]\s*)?(Abstract|Key\s*words?|\u6458\u8981|\u5173\u952e\u8bcd))(?=$|\s|[:\uff1a]|[\u3400-\u9fff])(\s*[:\uff1a])?', re.I)
SECTION = re.compile(r'^((?:\d{1,2}\.){0,2}\d{1,2})[.\u3001]?(?:\s+|(?<=[.\u3001])(?=[\u3400-\u9fff]))\S')
STANDARD_SECTION = re.compile(
    r'^(?:\d+(?:\.\d+)*[.\u3001]?\s*)?(?:introduction|background|materials and methods|methods|'
    r'results(?: and discussion)?|discussion|conclusions?|acknowledg[e]?ments?|references|bibliography|'
    r'\u5f15\u8a00|\u7eea\u8bba|\u7814\u7a76\u65b9\u6cd5|\u7ed3\u679c|\u8ba8\u8bba|\u7ed3\u8bba|\u53c2\u8003\u6587\u732e)$', re.I)
AFFILIATION = re.compile(r'\u5927\u5b66|\u5b66\u9662|\u7814\u7a76\u6240|\b(?:University|Institute|Laborator(?:y|ies)|College|Department)\b', re.I)
AUTHOR = re.compile(r'\s*[A-Z][A-Za-z.\'-]+(?:\s+[A-Z][A-Za-z.\'-]+){1,4}\s*')
CJK_BODY = re.compile(r'[\u3400-\u9fff]{4,}')
FIGURE_CAPTION = re.compile(r'^(?:\u56fe|fig(?:ure)?\.?)\s*\d', re.I)
TABLE_CAPTION = re.compile(r'^(?:\u8868|table)\s*\d', re.I)
KEYWORD_SEPARATOR = re.compile(r'\u3001|(?<!\d)\uff0c(?!\d)|(?<!\d),(?=\s+[^\s\d])')


def _child(element, tag):
    child = element.find(qn(tag))
    if child is None:
        child = OxmlElement(tag)
        element.append(child)
    return child


def _faces(properties, east_asia, latin=LATIN):
    fonts = properties.get_or_add_rFonts()
    fonts.attrib.clear()
    for script, name in (('ascii', latin), ('hAnsi', latin), ('cs', latin), ('eastAsia', east_asia)):
        fonts.set(qn('w:' + script), name)


def _style_document(doc):
    styles = doc.styles
    base = styles['Normal']
    for name, (chinese, size, bold) in STYLES.items():
        if name not in styles:
            styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        style = styles[name]
        if style != base:
            style.base_style = base
        style.next_paragraph_style = base
        props = style.element.get_or_add_rPr()
        for child in list(props):
            props.remove(child)
        _faces(props, chinese)
        style.font.size = Pt(size)
        _child(props, 'w:szCs').set(qn('w:val'), str(round(size * 2)))
        style.font.bold = bold
        style.font.color.rgb = RGBColor(0, 0, 0)
        fmt = style.paragraph_format
        fmt.alignment = (WD_ALIGN_PARAGRAPH.CENTER if name in {'Title', 'Heading 1', 'Academic Formula'}
                         else WD_ALIGN_PARAGRAPH.LEFT)
        if name == 'Normal':
            fmt.line_spacing = 1.5
        elif name.startswith('Heading'):
            fmt.keep_with_next = True

    label = styles.add_style('Academic Label', WD_STYLE_TYPE.CHARACTER)
    _faces(label.element.get_or_add_rPr(), 'SimHei')
    label.font.size = Pt(14)
    label.font.bold = True
    link = styles.add_style('Academic Link', WD_STYLE_TYPE.CHARACTER)
    link.font.color.rgb = RGBColor.from_string('0563C1')
    link.font.underline = True
    math = styles.add_style('Academic Math Glyph', WD_STYLE_TYPE.CHARACTER)
    _faces(math.element.get_or_add_rPr(), 'SimSun', MATH)
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Cm(2.54)
        section.left_margin = section.right_margin = Cm(3.17)


def _text(runs):
    return ''.join(r['text'] for r in runs)


def _join_blocks(blocks, role):
    runs = []
    for block in blocks:
        more = block['runs']
        if runs:
            boundary = runs[-1]['text'][-1:] + more[0]['text'][:1]
            if not any(c.isspace() for c in boundary) and not HAN.search(boundary):
                runs.append(dict(text=' ', flags=0, uri=None))
        runs.extend(more)
    return dict(blocks[0], runs=runs, word_role=role)


def _frontmatter(blocks):
    # Some old PDF-derived blocks joined the title, signed author list and first
    # institution. Split only when several affiliation superscripts and an
    # institution boundary independently support the break.
    limit = next((i for i, b in enumerate(blocks[:14]) if LABEL.match(_text(b.get('runs', [])).lstrip())), None)
    if not limit:
        return blocks
    first = blocks[0]
    runs = first.get('runs', [])
    if first.get('word_role') or first['kind'] != 'paragraph':
        return blocks
    # A separated front matter can be identified without the PDF point sizes:
    # an author line with multiple affiliations follows the title and precedes
    # a numbered institution line, with a distinct abstract below both.
    for i in range(1, min(limit - 1, 5)):
        names = blocks[i].get('runs', [])
        if not names or not AUTHOR.fullmatch(names[0]['text']):
            continue
        if sum(bool(r['flags'] & 1 and re.fullmatch(r'\d+', r['text'].strip())) for r in names) < 2:
            continue
        j = next((k for k in range(i+1, limit) if
                  re.match(r'^\d+\s*\D', _text(blocks[k].get('runs', [])).strip()) and
                  AFFILIATION.search(_text(blocks[k].get('runs', [])))), None)
        if j is not None and all(b.get('runs') and b['kind'] == 'paragraph' for b in blocks[:j]):
            return [_join_blocks(blocks[:i], 'main_title'), _join_blocks(blocks[i:j], 'author'), *blocks[j:]]
    if len(runs) < 7:
        return blocks
    begin = next((i for i in range(1, len(runs) - 1)
                  if AUTHOR.fullmatch(runs[i]['text']) and runs[i+1]['flags'] & 1
                  and re.fullmatch(r'\d+', runs[i+1]['text'].strip())), None)
    if begin is None or len(_text(runs[:begin]).strip()) < 12:
        return blocks
    for end in range(begin+4, len(runs)):
        previous = runs[end-1]
        marker = re.fullmatch(r'(.*\*)\s*(\d+)', previous['text'])
        if not (previous['flags'] & 1 and marker and AFFILIATION.search(_text(runs[end:]))):
            continue
        if sum(bool(r['flags'] & 1 and re.fullmatch(r'\d+', r['text'].strip())) for r in runs[begin:end]) < 2:
            continue
        return [dict(first, word_role='main_title', runs=runs[:begin]),
                dict(first, word_role='author', runs=runs[begin:end-1]+[dict(previous, text=marker[1])]),
                dict(first, word_role='affiliation', runs=[dict(previous, text=marker[2])]+runs[end:]),
                *blocks[1:]]
    return blocks


def _caption_order(blocks):
    result = []
    i = 0
    while i < len(blocks):
        a, b = blocks[i:i+2] if i+1 < len(blocks) else (blocks[i], None)
        if b and 'box' in a and 'box' in b:
            x0, _, x1, end = a['box']
            u0, start, u1, _ = b['box']
            close = 0 <= start-end <= 24 and min(x1, u1) - max(x0, u0) >= .5 * min(x1-x0, u1-u0)
            figure = a['kind'] == 'caption' and b['kind'] == 'image' and not b.get('word_role') and FIGURE_CAPTION.match(_text(a['runs']).lstrip())
            table = a['kind'] == 'table' and b['kind'] == 'caption' and TABLE_CAPTION.match(_text(b['runs']).lstrip())
            if close and (figure or table):
                result.extend((b, a))
                i += 2
                continue
        result.append(a)
        i += 1
    return result


def _prose_with_bad_script(runs):
    # A whole extracted prose span/phrase may be flagged as a script; a real
    # citation or symbol next to it must retain its independent formatting.
    bad = set()
    for direction, group in groupby(enumerate(runs), key=lambda pair:
                                    1 if pair[1]['flags'] & 1 else -int(bool(pair[1].get('subscript')))):
        group = list(group)
        if not direction:
            continue
        prose = [i for i, r in group if HAN.search(r['text'])]
        if prose and (len(prose) > 1 and len(HAN.findall(_text(r for _, r in group))) >= 5
                      or any(CJK_BODY.search(r['text']) for _, r in group)):
            bad.update(range(prose[0], prose[-1] + 1))
    return bad


def _add_link(paragraph, run, uri):
    run.style = 'Academic Link'
    hyperlink = OxmlElement('w:hyperlink')
    hyperlink.set(qn('r:id'), paragraph.part.relate_to(uri, RT.HYPERLINK, is_external=True))
    hyperlink.append(run._r)
    paragraph._p.append(hyperlink)


def _write_runs(paragraph, runs, archive, label_length=0):
    invalid_scripts = _prose_with_bad_script(runs)
    for i, span in enumerate(runs):
        if 'asset' in span:
            paragraph.add_run().add_picture(io.BytesIO(archive.read(span['asset'])), width=Pt(max(1, span['width'])))
            label_length = max(0, label_length - len(span['text']))
            continue
        flags = span['flags']
        text = span['text']
        prefix = text[:label_length]
        pieces = [(prefix, 'Academic Label')] if prefix else []
        pieces.extend((value, 'Academic Math Glyph' if part % 2 else None)
                      for part, value in enumerate(MATH_GLYPHS.split(text[len(prefix):])) if value)
        for value, style in pieces:
            run = paragraph.add_run(value, style=style)
            if flags & 2:
                run.italic = True
            if flags & 16 and paragraph.style.name != 'Heading 3':
                run.bold = True
            if flags & 1 and i not in invalid_scripts:
                run.font.superscript = True
            elif span.get('subscript') and (i not in invalid_scripts or flags & 1):
                run.font.subscript = True
            if span['uri']:
                _add_link(paragraph, run, span['uri'])
                if style == 'Academic Math Glyph':
                    _faces(run._r.get_or_add_rPr(), 'SimSun', MATH)
        label_length = max(0, label_length - len(text))


def _write_table(doc, rows):
    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    borders = _child(table._tbl.tblPr, 'w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        border = _child(borders, 'w:' + edge)
        border.set(qn('w:val'), 'single' if edge in {'top', 'bottom'} else 'nil')
        border.set(qn('w:sz'), '8')
    for row, cells in zip(table.rows, rows):
        for cell, text in zip(row.cells, cells):
            cell.text = text
    for cell in table.rows[0].cells:
        bottom = _child(_child(cell._tc.get_or_add_tcPr(), 'w:tcBorders'), 'w:bottom')
        bottom.set(qn('w:val'), 'single')
        bottom.set(qn('w:sz'), '4')


def _keyword_list(runs, offset):
    text = _text(runs)
    remainder = text[offset:]
    if len(remainder) > 220 or any(c in remainder for c in '()[]{}\uff08\uff09'):
        return runs
    positions = {offset + m.start() for m in KEYWORD_SEPARATOR.finditer(remainder)}
    if not positions:
        return runs
    result = []
    index = 0
    for span in runs:
        replacement = ''.join(('\uff1b' if c != ',' else ';') if index+j in positions else c
                              for j, c in enumerate(span['text']))
        result.append(dict(span, text=replacement))
        index += len(span['text'])
    return result


def write_word(archive, manifest, handle):
    doc = Document()
    _style_document(doc)
    width = doc.sections[0].page_width - doc.sections[0].left_margin - doc.sections[0].right_margin
    abstract = False
    front = True
    keyword_list_next = False
    for index, page in enumerate(manifest['pages']):
        blocks = _frontmatter(page['blocks']) if index == 0 else page['blocks']
        for block in _caption_order(blocks):
            kind = block['kind']
            explicit = ROLES.get(block.get('word_role'))
            if kind == 'table':
                _write_table(doc, block['rows'])
                continue
            if kind == 'image':
                p = doc.add_paragraph(style=explicit or 'Normal')
                p.add_run().add_picture(io.BytesIO(archive.read(block['asset'])),
                                        width=min(width, Pt(block['box'][2]-block['box'][0])))
                for uri in block.get('links', []):
                    p = doc.add_paragraph()
                    _add_link(p, p.add_run(uri), uri)
                continue
            runs = block['runs']
            raw = _text(runs)
            text = raw.lstrip()
            label = LABEL.match(text)
            number = SECTION.match(text)
            standard = STANDARD_SECTION.fullmatch(text.strip())
            is_heading = kind == 'heading' or standard or (
                number and len(text) < 95 and not text.endswith(('.', '\u3002'))
                and sum(len(r['text']) for r in runs if r['flags'] & 16) > len(text) / 2)
            label_length = len(raw)-len(text)+label.end() if label else 0
            preceding_keywords, keyword_list_next = keyword_list_next, False
            if label:
                abstract = label[2].casefold() in {'abstract', '\u6458\u8981'}
                style = 'Academic Abstract' if abstract else 'Academic Keywords'
                keyword_list_next = not abstract and not raw[label_length:].strip()
                front = False
            elif explicit:
                style = explicit
                if style.startswith('Heading'):
                    abstract = front = False
            elif kind == 'caption':
                style = 'Caption'
            elif is_heading:
                level = number[1].count('.') + 1 if number else (1 if standard or front else 2)
                style = f'Heading {level}'
                abstract = front = False
            elif preceding_keywords and len(text) <= 220:
                style = 'Academic Keywords'
            elif front and index == 0 and len(text) < 250 and AFFILIATION.search(text) and re.match(r'^\d+\s*\D', text):
                style = 'Academic Affiliation'
            else:
                style = 'Academic Abstract' if abstract else 'Normal'
            paragraph = doc.add_paragraph(style=style)
            if style == 'Academic Keywords':
                runs = _keyword_list(runs, label_length)
            _write_runs(paragraph, runs, archive, label_length)
        if manifest['issues'].get(str(page['source_page']-1)):
            doc.add_paragraph(f"\u539f\u7b2c {page['source_page']} \u9875\u542b\u672a\u7ffb\u8bd1\u533a\u57df\uff0c\u8bf7\u5bf9\u7167\u6b64\u7248\u672c PDF\u3002")
    doc.save(handle)
