from linguafloat.document_scientific import missing_body_translation


def test_crop_offset_table_excludes_only_cells_not_nearby_caption():
    import pymupdf as f
    from linguafloat.document_geometry import source_graphic_area, protected_pdf_box
    with f.open() as doc:
        p=doc.new_page(width=500,height=720);p.set_cropbox(f.Rect(41,12,450,660))
        cells=f.Rect(30,210,290,252);caption=f.Rect(30,196,240,205)
        mapped=source_graphic_area(p,dict(kind='table',box=list(cells)))
        native=f.Rect(protected_pdf_box(mapped,p.rect.height));assert native*p.transformation_matrix==cells
        assert not (native*p.transformation_matrix).intersects(caption)


def test_missing_measured_entity_is_not_a_valid_formula_fragment():
    from linguafloat.document_scientific import _formula_fragment_problem
    source='The concentration of VOCs was determined by gas chromatography.'
    assert _formula_fragment_problem(source,'采用气相色谱法。',[])
    assert not _formula_fragment_problem(source,'采用气相色谱法测定 VOC 浓度。',[])
    source='The concentration of VOCs was determined by gas chromatography (GC'
    assert _formula_fragment_problem(source,'采用气相色谱法（GC 测定 VOC 的浓度',[])
    assert not _formula_fragment_problem(source,'VOC 浓度采用气相色谱法测定（GC',[])


def test_unchanged_sentence_with_intact_formula_is_not_chinese():
    s='The samples were tested using the same settings as in {v1}.'
    assert missing_body_translation(s, s, 'zh-Hans')
    assert not missing_body_translation(s, '样品的测试设置与 {v1} 相同。', 'zh-Hans')
    assert not missing_body_translation('L. plantarum DX7, 12 mL, MS/MS', 'L. plantarum DX7, 12 mL, MS/MS', 'zh-Hans')
    assert not missing_body_translation(s, s, 'en')


def test_bibliography_padding_is_not_a_visible_crossing():
    from types import SimpleNamespace as N
    from linguafloat.document_references import filter_layout_paragraphs
    char=N(char_unicode='X',box=N(x=10,y=49,x2=14,y2=58))
    p=N(unicode='X',box=char.box,pdf_paragraph_composition=[N(pdf_character=char,pdf_formula=None)])
    page=N(page_number=0,cropbox=N(box=N(y2=100)),pdf_paragraph=[p]); failed=[]
    boundary={'page':0,'boxes':[[0,50,100,100]]}
    filter_layout_paragraphs(page,boundary,lambda *args: failed.append(args));assert page.pdf_paragraph==[p] and not failed
    char.box.y=45; filter_layout_paragraphs(page,boundary,lambda *args: failed.append(args));assert failed and not page.pdf_paragraph
