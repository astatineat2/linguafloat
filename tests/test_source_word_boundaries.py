from types import SimpleNamespace as NS
from linguafloat.document_richtext import normalize_prose_spaces


def test_source_spaces_restored_across_styles_without_guessing_or_joining_formulas():
    runs = [NS(unicode=t) for t in ('测得G. example', 'AB7和mmolTE/g；', 'ABC99。')]
    p = NS(unicode='G. example AB7 with mmol TE/g and ABC99.', pdf_paragraph_composition=[NS(pdf_same_style_unicode_characters=r) for r in runs])
    normalize_prose_spaces(p)
    assert ''.join(r.unicode for r in runs) == '测得G. example AB7和mmol TE/g；ABC99。'


def test_superscript_citation_after_author_does_not_swallow_decimal_integer():
    from babeldoc.format.pdf.document_il import Box, PdfCharacter, PdfFormula, PdfParagraphComposition as Part, PdfStyle
    from linguafloat.document_scientific import merge_formula_runs
    parts=[]
    def glyph(t,x,y,s):
        return PdfCharacter(char_unicode=t, box=Box(x,y,x+3,y+s),pdf_style=PdfStyle(font_size=s))
    for i,c in enumerate('Li et al.,'):
        parts.append(Part(pdf_character=glyph(c,i*3,10,9)))
    digs=[glyph(c,30+i*3,14,6) for i,c in enumerate('19')]
    parts.append(Part(pdf_formula=PdfFormula(pdf_character=digs,box=Box(30,14,36,20),y_offset=4)))
    parts.extend(Part(pdf_character=glyph(c,36+i*3,10,9)) for i,c in enumerate('0.8 mL'))
    result=merge_formula_runs(parts,9)
    assert [''.join(c.char_unicode for c in r.pdf_formula.pdf_character) for r in result if r.pdf_formula] == ['19']


def test_citation_before_decimal_stays_with_attribution_not_dosing_verb():
    from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder
    from linguafloat.document_scientific import check_formula_tokens
    slot=NS(y_offset=3, pdf_character=[NS(char_unicode='9')])
    ti=NS(unicode='Following Li et al.,{v1} 0.8 mL was added.',placeholders=[FormulaPlaceholder(1,slot,'{v1}','')])
    assert check_formula_tokens(ti,'参照Li等人的方法{v1}，将0.8 mL加入。')
    assert not check_formula_tokens(ti,'参照Li等人的方法，将{v1}0.8 mL加入。')
