from types import SimpleNamespace
import re

from babeldoc.format.pdf.document_il import (Box, Page, PdfFont, PdfParagraph, PdfStyle,
    PdfParagraphComposition as Part, PdfSameStyleCharacters, PdfFormula)
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator, RichTextPlaceholder, FormulaPlaceholder
from linguafloat.document_richtext import (remember_caption_markers, restore_source_styles,
    styled_translate_input, validate_styles, caption_brackets_match)
from test_document_geometry import char


def run(text, font='body', x=20, y=100, size=9):
    chars = [char(c, Box(x+i*5,y,x+i*5+4,y+size), size) for i,c in enumerate(text)]
    for c in chars:
        c.pdf_style.font_id = font
    return Part(pdf_same_style_characters=PdfSameStyleCharacters(
        pdf_style=chars[0].pdf_style, pdf_character=chars))


def caption():
    p=PdfParagraph(unicode='Heading. (value). d, Panel description.',layout_label='figure_caption',
        pdf_style=PdfStyle(font_id='body',font_size=9),pdf_paragraph_composition=[
            run('Heading.', 'bold'), run(' (value). '), run('d','bold'),run(', Panel description.')])
    return Page(pdf_paragraph=[p],pdf_font=[PdfFont(font_id='body',name='Source-Regular'),
        PdfFont(font_id='bold',name='Source-Bold')]),p


def engine():
    e=ILTranslator.__new__(ILTranslator)
    e.translate_engine=SimpleNamespace(get_formular_placeholder=lambda n:f'{{v{n}}}',
        get_rich_text_left_placeholder=lambda n:f'<style id="{n}">',
        get_rich_text_right_placeholder=lambda n:'</style>')
    return e


def test_wrapped_date_in_prose_is_sent_as_words_not_native_math():
    a=run('In',x=250,y=110).pdf_same_style_characters.pdf_character
    b=run('1998',x=20,y=98).pdf_same_style_characters.pdf_character
    formula=PdfFormula(pdf_character=a+b)
    p=PdfParagraph(unicode='Prior work. In 1998 the investigators reported changes.',layout_label='plain text',pdf_style=a[0].pdf_style,
        pdf_paragraph_composition=[run('Prior work.',font='bold'),Part(pdf_formula=formula),run(' the investigators reported changes.')])
    def text():return styled_translate_input(engine(),p,lambda *_:None).unicode
    assert 'In 1998' in text()
    assert p.pdf_paragraph_composition[1].pdf_formula is formula
    p.layout_label='table';assert '1998' not in text()
    p.layout_label='plain text';formula.is_corner_mark=True;assert '1998' not in text()
    formula.is_corner_mark=False;b[0].pdf_style.font_size=5;assert '1998' not in text()


def test_formula_extraction_retains_bold_prose_runs_and_caption_glyphs():
    page,p=caption()
    remembered=remember_caption_markers(page)
    original=[c for part in p.pdf_paragraph_composition for c in part.pdf_same_style_characters.pdf_character]
    # Reproduce the extraction boundary that converts style runs to bare glyphs.
    p.pdf_paragraph_composition=[Part(pdf_character=c) for c in original]
    restore_source_styles(page,remembered)
    actual=[c for part in p.pdf_paragraph_composition for c in
            (part.pdf_formula.pdf_character if part.pdf_formula else part.pdf_same_style_characters.pdf_character)]
    assert list(map(id,actual)) == list(map(id,original))
    ti=styled_translate_input(engine(),p,lambda *_:None)
    assert any(isinstance(x,RichTextPlaceholder) and x.composition.pdf_style.font_id=='bold' for x in ti.placeholders)
    marker=next(x for x in ti.placeholders if isinstance(x,FormulaPlaceholder))
    assert marker.formula.pdf_character[0].char_unicode=='d'
    assert marker.formula.pdf_character[0].pdf_style.font_id=='bold'


def test_caption_anchor_cannot_move_inside_parentheses():
    page,p=caption();restore_source_styles(page,remember_caption_markers(page))
    ti=styled_translate_input(engine(),p,lambda *_:None)
    token=next(x.placeholder for x in ti.placeholders if isinstance(x,FormulaPlaceholder))
    assert caption_brackets_match(ti,ti.unicode.replace('(','（').replace(')','）'))
    changed=re.sub(r'\)\s*\.\s*'+re.escape(token),'. '+token+')',ti.unicode)
    assert changed != ti.unicode
    assert not caption_brackets_match(ti,changed)


def test_missing_style_token_is_rejected_instead_of_publishing_plain_text():
    page,p=caption();restore_source_styles(page,remember_caption_markers(page))
    ti=styled_translate_input(engine(),p,lambda *_:None)
    assert validate_styles(ti,ti.unicode)
    tag=next(x.left_placeholder for x in ti.placeholders if isinstance(x,RichTextPlaceholder))
    assert not validate_styles(ti,ti.unicode.replace(tag,''))


def test_chinese_word_order_can_move_complete_sibling_styles():
    e=engine()
    p=PdfParagraph(unicode='L. plantarum responds to p-acid',pdf_style=PdfStyle(font_id='body',font_size=9),
        pdf_paragraph_composition=[run('L. plantarum','italic'),run(' responds to '),run('p','italic'),run('-acid')])
    ti=styled_translate_input(e,p,lambda *_:None)
    styles=[x for x in ti.placeholders if isinstance(x,RichTextPlaceholder)]
    first,second=styles
    output=f'{second.left_placeholder}p{second.right_placeholder}-酸影响{first.left_placeholder}L. plantarum{first.right_placeholder}'
    assert validate_styles(ti,output)


def test_reordered_style_validation_rejects_unknown_duplicate_and_changed_parents():
    from linguafloat.document_richtext import style_structure_matches
    source='<style id="1">a<style id="2">b</style></style><style id="3">c</style>'
    assert style_structure_matches(source,'<style id="3">丙</style><style id="1">甲<style id="2">乙</style></style>')
    assert not style_structure_matches(source,'<style id="1">甲</style><style id="2">乙</style><style id="3">丙</style>')
    assert not style_structure_matches(source,source+source)
    assert not style_structure_matches(source,source.replace('id="2"','id="4"'))
    assert not style_structure_matches(source,source.replace('</style>','',1))


def test_formula_fragment_accepts_sibling_reorder_but_keeps_boundary_styles():
    from linguafloat.document_scientific import _formula_fragment_problem
    source='</style>before <style id="2">L.</style> and <style id="4">p</style><style id="6">after'
    output='</style>此前<style id="4">p</style>和<style id="2">L.</style><style id="6">之后'
    assert not _formula_fragment_problem(source,output,[])
    assert _formula_fragment_problem(source,output.replace('</style>','',1),[])
    assert _formula_fragment_problem(source,output+'</style>',[])


def test_unchanged_scientific_name_keeps_translated_spaces_and_font_policy():
    from linguafloat.document_richtext import remap_unchanged_prose
    from babeldoc.format.pdf.document_il import PdfSameStyleUnicodeCharacters
    e=engine()
    source=run('Lactiplantibacillusplantarum','italic')
    p=PdfParagraph(unicode='induced Lactiplantibacillus plantarum DX7',
        pdf_style=PdfStyle(font_id='body',font_size=9),
        pdf_paragraph_composition=[run('induced '),source,run(' DX7')])
    ti=styled_translate_input(e,p,lambda *_:None)
    tag=next(x for x in ti.placeholders if isinstance(x,RichTextPlaceholder))
    output=f'诱导{tag.left_placeholder}Lactiplantibacillus plantarum{tag.right_placeholder} DX7'
    # The pinned parser returns original glyphs when only inferred spaces differ.
    p.pdf_paragraph_composition=[Part(pdf_same_style_characters=tag.composition)]
    remap_unchanged_prose(p,ti,output)
    converted=p.pdf_paragraph_composition[0].pdf_same_style_unicode_characters
    assert isinstance(converted,PdfSameStyleUnicodeCharacters)
    assert converted.unicode=='Lactiplantibacillus plantarum'
    assert converted.pdf_style.font_id=='italic'


def test_materially_equal_lazy_graphics_states_do_not_split_every_word():
    from babeldoc.format.pdf.document_il import GraphicState
    class DeferredState:
        def __str__(self):
            return '1 j 1 J 0 w'
    parts=[run(t,'body',x=20+i*30,size=9) for i,t in enumerate(('one',' ','two',' ','three'))]
    for part in parts:
        for c in part.pdf_same_style_characters.pdf_character:
            c.pdf_style.graphic_state=GraphicState(DeferredState())
    p=PdfParagraph(unicode='one two three',pdf_style=PdfStyle(font_id='body',font_size=9),
        pdf_paragraph_composition=parts)
    restore_source_styles(Page(pdf_paragraph=[p]))
    assert len(p.pdf_paragraph_composition)==1
    assert p.pdf_style.graphic_state is not None


def test_engine_dummy_spaces_without_xobject_do_not_fragment_prose():
    parts=[run('First','body'),run(' ','body'),run('second','body')]
    for part,xobj in zip(parts,(0,None,0)):
        for c in part.pdf_same_style_characters.pdf_character:c.xobj_id=xobj
    p=PdfParagraph(unicode='First second',pdf_style=PdfStyle(font_id='body',font_size=9),
        pdf_paragraph_composition=parts)
    restore_source_styles(Page(pdf_paragraph=[p]))
    assert len(p.pdf_paragraph_composition)==1
    # Actual glyphs from different form resources must still stay separate.
    parts[-1].pdf_same_style_characters.pdf_character[0].xobj_id=2
    p.pdf_paragraph_composition=parts
    restore_source_styles(Page(pdf_paragraph=[p]))
    assert len(p.pdf_paragraph_composition)>1


def test_cjk_spaces_across_style_runs_keep_latin_word_spaces_and_math():
    from babeldoc.format.pdf.document_il import PdfSameStyleUnicodeCharacters as U
    from linguafloat.document_richtext import normalize_prose_spaces
    def text(s):return Part(pdf_same_style_unicode_characters=U(unicode=s,pdf_style=PdfStyle(font_id='body',font_size=9)))
    p=PdfParagraph(pdf_paragraph_composition=[text('自由'),text(' '),text('基 '),text('清除'),
        text(' Lactiplantibacillus '),text('plantarum'),text(' （'),text('ILA'),text('）')])
    normalize_prose_spaces(p)
    assert ''.join(x.pdf_same_style_unicode_characters.unicode for x in p.pdf_paragraph_composition)=='自由基清除 Lactiplantibacillus plantarum （ILA）'


def test_many_math_tokens_do_not_disable_prose_styles():
    page,p=caption();restore_source_styles(page,remember_caption_markers(page))
    for i in range(45):
        c=char('x',Box(20,100,24,109))
        p.pdf_paragraph_composition.append(Part(pdf_formula=PdfFormula(box=c.box,pdf_character=[c])))
    ti=styled_translate_input(engine(),p,lambda *_:None)
    assert sum(isinstance(x,FormulaPlaceholder) for x in ti.placeholders)==46
    assert any(isinstance(x,RichTextPlaceholder) for x in ti.placeholders)


def test_italic_variable_and_bold_word_are_not_panel_identifiers():
    page,p=caption();p.pdf_paragraph_composition=[run('boldword','bold'),run('x','italic')]
    assert not remember_caption_markers(page)[id(p)]


def test_missing_style_without_math_is_local_failure_and_discards_cached_reply(tmp_path):
    from babeldoc.format.pdf.document_il import Cropbox
    from linguafloat.document_core import JobControl, JobStore
    from linguafloat.document_layout import LayoutPolicy
    page,p=caption()
    p.pdf_paragraph_composition=[run('Heading.', 'bold'),run(' Some description.')]
    p.box=Box(20,100,200,120)
    page.page_number=0
    page.cropbox=Cropbox(Box(0,0,600,800))
    ti=styled_translate_input(engine(),p,lambda *_:None)
    store=JobStore(tmp_path/'jobs.sqlite3')
    store.put('bad-reply','plain text without required style')
    policy=LayoutPolicy(JobControl())
    policy.scientific=SimpleNamespace(translator=SimpleNamespace(store=store))
    policy.paragraph_pages[id(p)]=page
    policy.request_keys[id(p)].add('bad-reply')
    try:
        with policy:
            ILTranslator.post_translate_paragraph(engine(),p,None,ti,'无格式的译文')
        assert id(p) in policy.failed_paragraphs
        assert store.get('bad-reply') is None
        assert policy.retained_regions[0]
    finally:
        store.close()


def test_caption_marker_serializes_in_pinned_schema_and_is_recovered_after_cache_load():
    import json
    from babeldoc.format.pdf.document_il import Document
    from babeldoc.format.pdf.document_il.xml_converter import XMLConverter
    from linguafloat.document_artifacts import _decoder
    from linguafloat.document_richtext import restore_cached_caption_markers
    page,p=caption();restore_source_styles(page,remember_caption_markers(page))
    data=json.loads(XMLConverter().to_json(Document(page=[page])))
    replay=_decoder(Document)(data).page[0]
    restore_cached_caption_markers(replay)
    marker=next(part.pdf_formula for part in replay.pdf_paragraph[0].pdf_paragraph_composition if part.pdf_formula)
    assert marker._lf_caption_marker
    assert marker.pdf_character[0].pdf_style.font_id=='bold'
    assert marker.pdf_character[0].char_unicode=='d'
