"""Preserve source prose styles and immutable caption identifiers across translation."""
from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import dataclass
from babeldoc.format.pdf.document_il import PdfFormula


@dataclass(slots=True)
class CaptionMarker(PdfFormula):
    _lf_caption_marker = True


@dataclass(slots=True)
class SourceIdentity(CaptionMarker):
    """Native author identity carried through the existing immutable anchor path."""
    _lf_source_identity = True


def protect_author_identities(page, structure, *, cached=False):
    from dataclasses import fields
    from babeldoc.format.pdf.document_il import Box, PdfParagraphComposition as Part, PdfSameStyleCharacters
    from linguafloat.document_geometry import owns, union
    from linguafloat.document_scientific import composition_chars
    height = page.cropbox.box.y2
    protected = 0
    for author in structure.get('authors', []):
        x0,y0,x1,y1 = author['name_box']
        box = Box(x0-.3,height-y1-.3,x1+.3,height-y0+.3)
        for paragraph in page.pdf_paragraph:
            parts = paragraph.pdf_paragraph_composition
            chars = [c for part in parts for c in composition_chars(part) if owns(box,c)]
            if ''.join(c.char_unicode or '' for c in chars).strip() != author['name']:
                continue
            selected = {id(c) for c in chars}
            if cached:
                for part in parts:
                    f = part.pdf_formula
                    if f and not f.pdf_curve and not f.pdf_form and {id(c) for c in f.pdf_character} == selected:
                        part.pdf_formula = SourceIdentity(**{field.name:getattr(f,field.name) for field in fields(PdfFormula)})
                continue
            # A mixed formula may have extra spatial structure: never cut it.
            if any(part.pdf_formula and any(id(c) in selected for c in composition_chars(part))
                   and (part.pdf_formula.pdf_curve or part.pdf_formula.pdf_form
                        or any(id(c) not in selected for c in composition_chars(part))) for part in parts):
                continue
            rebuilt=[]; anchor=[]; inserted=False
            def flush():
                nonlocal inserted
                if anchor:
                    rebuilt.append(Part(pdf_formula=SourceIdentity(box=union([c.box for c in anchor]),
                        pdf_character=list(anchor), x_offset=0, y_offset=0, x_advance=0)))
                    anchor.clear(); inserted=True
            for part in parts:
                group=composition_chars(part)
                if not any(id(c) in selected for c in group):
                    flush(); rebuilt.append(part); continue
                for char in group:
                    if id(char) in selected:
                        anchor.append(char)
                    else:
                        flush()
                        rebuilt.append(Part(pdf_same_style_characters=PdfSameStyleCharacters(
                            box=copy.copy(char.box),pdf_style=copy.copy(char.pdf_style),pdf_character=[char])))
            flush()
            if inserted:
                paragraph.pdf_paragraph_composition=rebuilt; protected+=1
    return protected


def caption_formula(char):
    # The upstream IL classes use slots. A carrier subclass keeps the pinned
    # serialized schema unchanged; cached anchors are reidentified below.
    return CaptionMarker(box=copy.copy(char.box), pdf_character=[char],
                         x_offset=0, y_offset=0, x_advance=0)


def _style_key(style):
    if style is None:
        return None
    state = style.graphic_state.passthrough_per_char_instruction if style.graphic_state else None
    # Deferred instructions are distinct objects even when they draw identically.
    # Compare their complete materialized value; retain colours and clipping.
    return (style.font_id, round(style.font_size or 0, 6), str(state) if state is not None else None)


def remember_caption_markers(page):
    from linguafloat.document_scientific import composition_chars
    fonts = {f.font_id: f.name or '' for f in page.pdf_font}
    for obj in page.pdf_xobject:
        fonts.update({(obj.xobj_id, f.font_id): f.name or '' for f in obj.pdf_font})
    remembered = {}
    for paragraph in page.pdf_paragraph:
        markers = set()
        if paragraph.layout_label == 'figure_caption':
            for part in paragraph.pdf_paragraph_composition:
                chars = composition_chars(part)
                text = ''.join(c.char_unicode or '' for c in chars).strip()
                if not re.fullmatch(r'[a-z](?:\s*[,–−-]\s*[a-z])*', text):
                    continue
                letters = [c for c in chars if (c.char_unicode or '').isalpha()]
                if letters and all(c.pdf_style and re.search(r'bold|semibold|demi',
                    fonts.get((c.xobj_id, c.pdf_style.font_id), fonts.get(c.pdf_style.font_id, '')), re.I)
                    for c in letters):
                    markers.update(id(c) for c in letters)
        remembered[id(paragraph)] = markers
    return remembered


def restore_source_styles(page, remembered=None):
    """Reconstitute style runs after math extraction, without changing glyph ownership."""
    from babeldoc.format.pdf.document_il import PdfFormula, PdfParagraphComposition as Part, PdfSameStyleCharacters
    from linguafloat.document_geometry import union
    from linguafloat.document_scientific import composition_chars
    for paragraph in page.pdf_paragraph:
        markers = (remembered or {}).get(id(paragraph), set())
        result, run = [], []

        def flush():
            if run:
                result.append(Part(pdf_same_style_characters=PdfSameStyleCharacters(
                    box=union([c.box for c in run]), pdf_style=copy.copy(run[0].pdf_style),
                    pdf_character=list(run))))
                run.clear()

        def marker(char):
            flush()
            formula = caption_formula(char)
            result.append(Part(pdf_formula=formula))

        for part in paragraph.pdf_paragraph_composition:
            chars = composition_chars(part)
            if part.pdf_formula:
                flush()
                # A compound with graphics cannot be cut safely here. The math
                # repair owns that case; never discard its rules or forms.
                f = part.pdf_formula
                if not any(id(c) in markers for c in chars) or f.pdf_curve or f.pdf_form:
                    result.append(part)
                    continue
                chunk = []
                def formula_chunk():
                    if chunk:
                        clone = copy.copy(f)
                        clone.pdf_character = list(chunk)
                        clone.box = union([c.box for c in chunk])
                        clone.y_offset = (f.y_offset or 0) + clone.box.y - f.box.y
                        result.append(Part(pdf_formula=clone))
                        chunk.clear()
                for c in chars:
                    if id(c) in markers:
                        formula_chunk()
                        marker(c)
                    else:
                        chunk.append(c)
                formula_chunk()
            elif chars:
                for c in chars:
                    if id(c) in markers and c.box:
                        marker(c)
                    else:
                        previous = next((v for v in reversed(run) if (v.char_unicode or '').strip()), None)
                        resource_change = (previous is not None and (c.char_unicode or '').strip()
                                           and previous.xobj_id != c.xobj_id)
                        # The engine's synthetic word spaces have no xobject.
                        # They carry no painted glyph and must not split every
                        # word into a separate translation style placeholder.
                        if run and (_style_key(run[-1].pdf_style) != _style_key(c.pdf_style) or resource_change):
                            flush()
                        run.append(c)
            else:
                flush()
                result.append(part)
        flush()
        paragraph.pdf_paragraph_composition = result
        if paragraph.pdf_style and _style_key(paragraph.pdf_style)[2] is None:
            # The extractor sometimes drops base graphic state but retains it on
            # every glyph. Recover that base from the dominant matching run.
            candidates = [p.pdf_same_style_characters for p in result if p.pdf_same_style_characters
                          and _style_key(p.pdf_same_style_characters.pdf_style)[:2] == _style_key(paragraph.pdf_style)[:2]]
            weights = Counter()
            for item in candidates:
                weights[_style_key(item.pdf_style)] += len(item.pdf_character)
            if weights:
                wanted = weights.most_common(1)[0][0]
                paragraph.pdf_style = copy.copy(next(c.pdf_style for c in candidates if _style_key(c.pdf_style) == wanted))


def restore_cached_caption_markers(page):
    remembered = remember_caption_markers(page)
    for paragraph in page.pdf_paragraph:
        ids = remembered[id(paragraph)]
        for part in paragraph.pdf_paragraph_composition:
            f = part.pdf_formula
            if f and len(f.pdf_character) == 1 and id(f.pdf_character[0]) in ids and not f.pdf_curve and not f.pdf_form:
                part.pdf_formula = caption_formula(f.pdf_character[0])


def styled_translate_input(engine, paragraph, original, *args, **kwargs):
    """Keep style placeholders even when a scientific paragraph has many formulas."""
    from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
    from babeldoc.format.pdf.document_il.utils.layout_helper import get_char_unicode_string
    from linguafloat.document_scientific import composition_chars
    parts = paragraph.pdf_paragraph_composition
    # Pure-formula/numeric paragraphs and unicode-only cache data retain the
    # engine's skip rules. Only rich source runs require this adapter.
    rich = [p.pdf_same_style_characters for p in parts if p.pdf_same_style_characters]
    if not rich or not any(_style_key(r.pdf_style) != _style_key(paragraph.pdf_style) for r in rich):
        return original(engine, paragraph, *args, **kwargs)
    original_tokens = {}
    for name in ('_formula_placeholder_pattern', '_style_left_placeholder_pattern', '_style_right_placeholder_pattern'):
        pattern = getattr(engine, name, None)
        if pattern:
            for match in pattern.finditer(paragraph.unicode or ''):
                token = match.group(0)
                original_tokens[token] = original_tokens.get(token, 0) + 1
    tokens, placeholders, next_id = [], [], 1
    for part in parts:
        if part.pdf_formula:
            item = engine.process_formula(part.pdf_formula, next_id, paragraph)
            placeholders.append(item)
            tokens.append(item.placeholder)
            next_id = item.id + 1
        elif part.pdf_same_style_characters and _style_key(part.pdf_same_style_characters.pdf_style) != _style_key(paragraph.pdf_style):
            item = engine.process_composition(part.pdf_same_style_characters, next_id, paragraph)
            placeholders.append(item)
            tokens.extend([item.left_placeholder, *item.composition.pdf_character, item.right_placeholder])
            next_id = item.id + 2
        else:
            tokens.extend(composition_chars(part))
    value = ILTranslator.TranslateInput(get_char_unicode_string(tokens), placeholders, paragraph.pdf_style)
    value.set_original_placeholder_tokens(original_tokens)
    return value


def style_structure_matches(source, output, *, fragments=False):
    """Allow sibling spans to move with prose, while retaining the same tree.

    Formula fragments may start/end inside an existing span. Their dangling
    boundary stack must stay identical; it cannot move across a formula slot.
    """
    pattern = re.compile(r'</?([A-Za-z][\w:-]*)\b[^<>]*>')

    def structure(text):
        tags = list(pattern.finditer(text))
        stack, exits, nodes = [], [], []
        for tag in tags:
            token, name = tag.group(), tag.group(1)
            if token.startswith('</'):
                if stack:
                    if stack[-1][1] != name:
                        return None
                    stack.pop()
                else:
                    if not fragments:
                        return None
                    exits.append(token)
            else:
                nodes.append((token, tuple(x[0] for x in stack), tuple(exits)))
                if not token.endswith('/>'):
                    stack.append((token, name))
        if stack and not fragments:
            return None
        return (Counter(t.group() for t in tags), Counter(nodes), tuple(exits), tuple(stack))

    before, after = structure(source), structure(output)
    return before is not None and before == after


def validate_styles(translate_input, output):
    from babeldoc.format.pdf.document_il.midend.il_translator import RichTextPlaceholder
    tokens = [token for p in translate_input.placeholders if isinstance(p, RichTextPlaceholder)
              for token in (p.left_placeholder, p.right_placeholder)]
    if not tokens:
        return True
    return style_structure_matches(translate_input.unicode, output)


def remap_unchanged_prose(paragraph, translate_input, output):
    """Keep response spaces and chosen fonts for unchanged styled prose.

    The pinned parser reuses native glyphs if its space-stripped text matches.
    Those glyphs need not contain PDF's implicit word spaces and bypass the
    configured font mapper. Mathematical/identity anchors stay untouched.
    """
    from babeldoc.format.pdf.document_il import PdfSameStyleUnicodeCharacters
    from babeldoc.format.pdf.document_il.midend.il_translator import RichTextPlaceholder
    by_composition = {}
    for placeholder in translate_input.placeholders:
        if not isinstance(placeholder, RichTextPlaceholder):
            continue
        match = re.search(re.escape(placeholder.left_placeholder) + '(.*?)' +
                          re.escape(placeholder.right_placeholder), output, re.S)
        if match and not re.search(r'</?[A-Za-z][^<>]*>', match[1]):
            by_composition[id(placeholder.composition)] = match[1]
    for part in paragraph.pdf_paragraph_composition:
        source = part.pdf_same_style_characters
        if source is not None and id(source) in by_composition:
            part.pdf_same_style_unicode_characters = PdfSameStyleUnicodeCharacters(
                unicode=by_composition[id(source)], pdf_style=copy.copy(source.pdf_style))
            part.pdf_same_style_characters = None


def normalize_prose_spaces(paragraph):
    """Normalize ASCII spaces across adjacent translated runs, never formulas."""
    groups, runs = [], []
    for part in paragraph.pdf_paragraph_composition:
        if part.pdf_same_style_unicode_characters:
            runs.append(part.pdf_same_style_unicode_characters)
        elif runs:
            groups.append(runs)
            runs = []
    if runs:
        groups.append(runs)
    changed = 0
    han = r'\u3400-\u9fff\U00020000-\U000323af'
    pattern = re.compile(f'(?<=[{han}]) +(?=[{han}，。；：！？、）】])|(?<=[（【]) +(?=[{han}])')
    for group in groups:
        text = ''.join(r.unicode or '' for r in group)
        removed = {i for m in pattern.finditer(text) for i in range(m.start(), m.end())}
        offset = 0
        for run in group:
            old = run.unicode or ''
            run.unicode = ''.join(c for i,c in enumerate(old, offset) if i not in removed)
            offset += len(old)
            changed += old != run.unicode
    return changed


def caption_brackets_match(translate_input, output):
    """A panel identifier cannot migrate into or out of a parenthesis."""
    from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder
    formulas = {p.placeholder: p.formula for p in translate_input.placeholders if isinstance(p, FormulaPlaceholder)}
    anchors = {t for t, f in formulas.items() if getattr(f, '_lf_caption_marker', False)}
    if not anchors:
        return True
    token_pattern = re.compile('|'.join(re.escape(t) for t in sorted(formulas, key=len, reverse=True)))
    def signatures(text):
        normalized = text.translate(str.maketrans('（）［］｛｝', '()[]{}'))
        stack, result = [], []
        parts = re.split('(' + token_pattern.pattern + ')', normalized)
        for piece in parts:
            if piece in anchors:
                result.append((piece, tuple(stack)))
            if piece in formulas:
                piece = ''.join(c.char_unicode or '' for c in formulas[piece].pdf_character)
            for c in re.sub(r'<[^>]+>', '', piece):
                if c in '([{':
                    stack.append(c)
                elif c in ')]}':
                    if stack and stack[-1] == {')':'(', ']':'[', '}':'{'}[c]:
                        stack.pop()
        return result
    return signatures(translate_input.unicode) == signatures(output)
