"""Geometry-based formula grouping; no translation service or font substitution."""
import copy
import os
import re
from types import SimpleNamespace as NS

import pytest
from babeldoc.format.pdf.document_il import (
    Box, PdfCharacter, PdfFormula, PdfParagraph, PdfParagraphComposition as Part,
    PdfStyle, VisualBbox,
)
from linguafloat.document_scientific import composition_chars, protect_math


def glyph(text, x, y=100, size=10, font="body", order=None, scope=0):
    box = Box(x, y, x+size*.5, y+size)
    return PdfCharacter(char_unicode=text, box=box, visual_bbox=VisualBbox(copy.copy(box)),
                        pdf_style=PdfStyle(font_id=font, font_size=size),
                        pdf_character_id=ord(text[0]), xobj_id=scope,
                        render_order=order, sub_render_order=0)


def formula(chars):
    return Part(pdf_formula=PdfFormula(pdf_character=list(chars),
        box=Box(min(c.box.x for c in chars), min(c.box.y for c in chars),
                max(c.box.x2 for c in chars), max(c.box.y2 for c in chars)),
        x_offset=0, y_offset=0, x_advance=0))


def paragraph(parts):
    return PdfParagraph(pdf_style=PdfStyle(font_id="body", font_size=10),
                        unicode="source", pdf_paragraph_composition=parts)


def owner(p, chars):
    ids = {id(c) for c in chars}
    owners = [part.pdf_formula for part in p.pdf_paragraph_composition
              if part.pdf_formula and any(id(c) in ids for c in composition_chars(part))]
    assert len(owners) == 1
    assert ids <= {id(c) for c in owners[0].pdf_character}
    return owners[0]


@pytest.mark.parametrize("operator,brackets", [("GL", "[]"), ("SU", "()"), ("sin", "{}")])
@pytest.mark.parametrize("seed_first", [False, True])
def test_late_painted_nested_scripts_and_operator_form_one_rigid_unit(operator, brackets, seed_first):
    # Paint order is intentionally different from x order and parser order.
    base = glyph("A", 10, order=0)
    operator_chars = [glyph(t, 40+5*i, order=i+1) for i,t in enumerate(operator+brackets[0]+"n"+brackets[1])]
    scripts = [glyph("B", 16, 106, 6, order=20), glyph("D", 25, 106, 6, order=21),
               glyph("i", 15, 97, 6, order=22), glyph("j", 20, 104, 4, order=23),
               glyph("⋊", 32, font="math", order=24)]
    originals = [base, *operator_chars, *scripts]
    before = copy.deepcopy(originals)
    seed = formula(scripts[2:])
    parts = [Part(pdf_character=base), *[Part(pdf_character=c) for c in operator_chars],
             formula(scripts[:1]), Part(pdf_character=scripts[1])]
    p = paragraph(([seed]+parts) if seed_first else (parts+[seed]))
    protect_math(p, {"body":"OrdinaryText", "math":"Symbol"})
    merged = owner(p, originals)
    assert merged.pdf_character == originals  # original paint order, not guessed text
    assert originals == before
    from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
    moved = TypesettingUnit(formular=merged).relocate(200, 300, .8).formular
    offsets = [(b.box.x-a.box.x*.8, b.box.y-a.box.y*.8) for a,b in zip(originals,moved.pdf_character)]
    assert all(x == pytest.approx(offsets[0][0]) and y == pytest.approx(offsets[0][1]) for x,y in offsets)
    assert [(c.pdf_character_id,c.pdf_style.font_id,c.xobj_id) for c in moved.pdf_character] == [
        (c.pdf_character_id,c.pdf_style.font_id,c.xobj_id) for c in originals]
    protect_math(p, {"body":"OrdinaryText", "math":"Symbol"})
    assert owner(p, originals).pdf_character == originals


@pytest.mark.parametrize("citation", [False, True])
def test_baseline_punctuation_and_panel_letter_are_not_absorbed(citation):
    math = [glyph("5" if citation else "x", 10, 106 if citation else 100, 6 if citation else 10)]
    tail = [glyph(t, 15+5*i) for i,t in enumerate(". d, ")]
    p = paragraph([formula(math), *[Part(pdf_character=c) for c in tail]])
    protect_math(p, {"body":"OrdinaryText"})
    assert all(c not in owner(p, math).pdf_character for c in tail if c.char_unicode.strip())


@pytest.mark.parametrize("other_y,other_scope", [(112,0), (100,7)])
def test_different_line_or_xobject_does_not_join(other_y, other_scope):
    a, b = glyph("α",10,font="math"), glyph("x",16,other_y,scope=other_scope)
    p = paragraph([formula([a]),Part(pdf_character=b)])
    protect_math(p,{"math":"Symbol", "body":"OrdinaryText"})
    assert b not in owner(p,[a]).pdf_character


@pytest.mark.parametrize("cached", [False, True])
def test_caption_anchor_is_a_merge_boundary_even_when_geometry_touches(cached):
    from linguafloat.document_scientific import merge_formula_runs
    a, label = glyph("x",10), glyph("d",16,font="bold")
    left, marker = formula([a]), formula([label])
    if not cached:
        # BabelDOC's base dataclass is slotted; runtime annotation requires a
        # carrier subclass (the caption integration owns that representation).
        class CaptionFormula(PdfFormula):
            pass
        marker.pdf_formula=CaptionFormula(**{name:getattr(marker.pdf_formula,name)
                                             for name in marker.pdf_formula.__dataclass_fields__})
        marker.pdf_formula._lf_caption_marker = True
    comma = Part(pdf_character=glyph(",",21))
    p=paragraph([left,marker,comma])
    protect_math(p,{"body":"Text", "bold":"Text-Semibold"})
    assert owner(p,[label]) is marker.pdf_formula
    assert owner(p,[a]) is not marker.pdf_formula
    # attach_formula_rules invokes this public helper again, without a font map.
    again=merge_formula_runs(p.pdf_paragraph_composition,10)
    assert next(part.pdf_formula for part in again if part.pdf_formula is marker.pdf_formula) is marker.pdf_formula


def roundtrip(paragraph, target):
    """Exercise real placeholder generation/recovery without an API or tokenizer."""
    from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator, FormulaPlaceholder
    engine=ILTranslator.__new__(ILTranslator)
    engine.translate_engine=NS(get_formular_placeholder=lambda n: "{v"+str(n)+"}")
    engine._formula_placeholder_pattern=re.compile(r"\{v\d+\}")
    engine._style_left_placeholder_pattern=re.compile(r"<s\d+>")
    engine._style_right_placeholder_pattern=re.compile(r"</s\d+>")
    value=engine.get_translate_input(paragraph,disable_rich_text_translate=True)
    assert value is not None
    holders=[p for p in value.placeholders if isinstance(p,FormulaPlaceholder)
             and any(c is target[0] for c in p.formula.pdf_character)]
    assert len(holders)==1
    holder=holders[0]
    assert all(any(c is t for c in holder.formula.pdf_character) for t in target)
    # Translate prose synthetically while preserving every generated token.
    output=re.sub(r"\{v\d+\}|[A-Za-z]+",lambda m:m[0] if m[0].startswith("{") else "译",value.unicode)
    paragraph.pdf_paragraph_composition=engine.parse_translate_output(value,output)
    assert owner(paragraph,target) is holder.formula
    return holder.formula


# Captured native source p6, bottom-left PDF coordinates. Paint order is
# Z SO(2) C T 2 2 ⋉, while geometry encodes Z_2^(C_2 T) ⋉ SO(2).
# Keep font/glyph codes: the operator is STIXGeneral glyph 31, not a text font.
HARDING_GLYPHS = [
    ("Z",90,"T1_4",8.352,(39.973,251.480,44.842,259.832)),
    ("S",83,"T1_3",8.352,(67.333,251.480,71.968,259.832)),
    ("O",79,"T1_3",8.352,(71.943,251.480,78.341,259.832)),
    ("(",40,"T1_3",8.352,(78.274,251.480,81.348,259.832)),
    ("2",50,"T1_3",8.352,(81.440,251.480,85.966,259.832)),
    (")",41,"T1_3",8.352,(86.053,251.480,89.127,259.832)),
    ("C",67,"T1_4",5.760,(46.309,255.800,49.926,261.560)),
    ("T",84,"T1_4",5.760,(52.645,255.800,56.066,261.560)),
    ("2",50,"T1_3",5.760,(45.157,249.464,48.279,255.224)),
    ("2",50,"T1_3",4.896,(50.053,254.360,52.707,259.256)),
    ("⋉",31,"T1_5",8.352,(58.981,251.480,65.746,259.832)),
]


def il_formula_signature(formula):
    """Audit glyph codes/scope/order and scale-independent relative geometry."""
    chars=[c for c in formula.pdf_character if (c.char_unicode or "").strip()]
    scale=max(c.pdf_style.font_size for c in chars)
    x,y=min(c.box.x for c in chars),min(c.box.y for c in chars)
    return [(c.char_unicode,c.pdf_character_id,c.pdf_style.font_id,c.xobj_id,
             tuple(round(v/scale,5) for v in (c.pdf_style.font_size,c.box.x-x,c.box.y-y,
                                             c.box.x2-x,c.box.y2-y))) for c in chars]


def raw_glyph_records(page, region, ignore=""):
    """Select origins in a top-left PDF region, retaining font, size and origin."""
    result=[]
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines",[]):
            for span in line["spans"]:
                font=re.sub(r"^[A-Z]{6}\+","",span["font"])
                for char in span["chars"]:
                    x,y=char["origin"]
                    if (region[0]<=x<=region[2] and region[1]<=y<=region[3]
                            and char["c"].strip() and char["c"] not in ignore):
                        result.append(dict(text=char["c"],font=font,size=span["size"],x=x,y=y))
    return result


def match_raw_formula(source, output, tolerance=.15):
    """Find complete rigid/scaled matches; missing scripts or font swaps fail.

    The output region may include surrounding prose. This verifies visible
    placement; rawdict cannot certify glyph IDs/XObject scopes (use IL above).
    """
    if not source:
        raise ValueError("An original formula glyph capture is required")
    anchor=max(source,key=lambda c:c["size"])
    matches=[]
    for candidate in output:
        if (candidate["text"],candidate["font"]) != (anchor["text"],anchor["font"]):
            continue
        scale=candidate["size"]/anchor["size"]
        dx,dy=candidate["x"]-anchor["x"]*scale,candidate["y"]-anchor["y"]*scale
        used=set()
        for expected in source:
            found=next((i for i,c in enumerate(output) if i not in used
                        and (c["text"],c["font"])==(expected["text"],expected["font"])
                        and abs(c["size"]-expected["size"]*scale)<tolerance
                        and abs(c["x"]-expected["x"]*scale-dx)<tolerance
                        and abs(c["y"]-expected["y"]*scale-dy)<tolerance),None)
            if found is None:
                break
            used.add(found)
        else:
            matches.append(dict(scale=scale,dx=dx,dy=dy,glyphs=len(used)))
    return matches


def test_render_audit_rejects_lost_nested_script_font_swap_and_displacement():
    source=[dict(text=t,font=f,size=s,x=b[0],y=-b[1]) for t,_,f,s,b in HARDING_GLYPHS]
    output=[dict(c,size=c["size"]*.9,x=c["x"]*.9+600,y=c["y"]*.9+500) for c in source]
    assert len(match_raw_formula(source,output))==1
    assert not match_raw_formula(source,output[:9]+output[10:])
    altered=copy.deepcopy(output); altered[9]["y"]+=2
    assert not match_raw_formula(source,altered)
    altered=copy.deepcopy(output); altered[0]["font"]="ReplacementSerif"
    assert not match_raw_formula(source,altered)


def test_harding_captured_parser_fragments():
    chars=[PdfCharacter(char_unicode=t,pdf_character_id=code,
           pdf_style=PdfStyle(font_id=font,font_size=size),box=Box(*box),
           visual_bbox=VisualBbox(Box(*box)),xobj_id=0,render_order=i,sub_render_order=0)
           for i,(t,code,font,size,box) in enumerate(HARDING_GLYPHS)]
    originals=copy.deepcopy(chars)
    p=paragraph([*[Part(pdf_character=c) for c in chars[:6]],formula(chars[6:7]),
                 Part(pdf_character=chars[7]),formula(chars[8:])])
    p.pdf_style.font_size=8.25
    protect_math(p,{"T1_3":"HardingText-Regular","T1_4":"HardingText-RegularItalic",
                    "T1_5":"QNAKPI+STIXGeneral-Regular"})
    merged=owner(p,chars)
    assert merged.pdf_character==chars
    assert chars==originals
    assert merged.y_offset==pytest.approx(-2.016)
    from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
    moved=TypesettingUnit(formular=merged).relocate(150,200,.9).formular
    assert il_formula_signature(moved)==il_formula_signature(merged)


@pytest.mark.parametrize("page_number,region,layout", [
    (3,(225,592,271,606),(44,539,290,692)),
    (6,(39,248,90,262),(39,180,298,305)),
])
def test_native_source_fragments(page_number, region, layout, tmp_path, monkeypatch):
    source = os.environ.get("LINGUAFLOAT_SPIN_PDF")
    if not source:
        pytest.skip("Set LINGUAFLOAT_SPIN_PDF for the local two-region parser probe")
    import socket
    import pymupdf
    from babeldoc.format.pdf.parse_shared import build_parse_only_config
    from babeldoc.format.pdf.new_parser.native_parse import parse_prepared_pdf_with_new_parser_to_legacy_ir
    from babeldoc.format.pdf.document_il import PageLayout
    from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
    from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas
    for name in ("connect", "connect_ex"):
        original = getattr(socket.socket,name)
        def offline(sock,address,original=original):
            if isinstance(address,tuple) and address[0] in ("127.0.0.1","::1"):
                return original(sock,address)  # Windows asyncio self-pipe
            raise AssertionError("External networking is forbidden")
        monkeypatch.setattr(socket.socket,name,offline)
    with pymupdf.open(source) as pdf:
        cfg=build_parse_only_config(source,pages=str(page_number),working_dir=tmp_path)
        docs=parse_prepared_pdf_with_new_parser_to_legacy_ir(source,config=cfg,doc_pdf=pdf)
        page=docs.page[0]
        target=[c for c in page.pdf_character if c.box and region[0]<c.box.x<region[2]
                and region[1]<c.box.y<region[3] and (c.char_unicode or '').strip()
                and c.char_unicode != ',']
        assert len(target)==11
        before=copy.deepcopy(target)
        page.page_layout=[PageLayout(id=1,box=Box(*layout),class_name="plain text",conf=1)]
        page.pdf_character=[c for c in page.pdf_character if c.box and layout[0]<c.box.x<layout[2]
                            and layout[1]<c.box.y<layout[3]]
        ParagraphFinder(cfg).process_page(page); StylesAndFormulas(cfg).process_page(page)
        p=next(p for p in page.pdf_paragraph if any(c is target[0] for part in p.pdf_paragraph_composition for c in composition_chars(part)))
        fonts={f.font_id:f.name for f in page.pdf_font}
        protect_math(p,fonts)
        merged=owner(p,target)
        assert target==before
        assert [c for c in merged.pdf_character if any(c is t for t in target)]==target
        assert roundtrip(p,target) is merged
        from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
        moved=TypesettingUnit(formular=merged).relocate(200,300,.9).formular
        offsets=[(b.box.x-a.box.x*.9,b.box.y-a.box.y*.9) for a,b in zip(merged.pdf_character,moved.pdf_character)]
        assert all(x==pytest.approx(offsets[0][0]) and y==pytest.approx(offsets[0][1]) for x,y in offsets)
        if page_number==6:
            raw=raw_glyph_records(pdf[5],(39,530,90,543),ignore=",")
            assert len(raw)==11
            assert len(match_raw_formula(raw,raw))==1
