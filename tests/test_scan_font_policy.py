"""Chosen-font scan overlays, using real PDF rendering and offline fixtures."""
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pymupdf as f
import pytest

from linguafloat.document_core import DocumentError, JobControl
from linguafloat.document_scan_layout import fit_translation, translate_scan_page


def region(recovered=False, **fonts):
    return dict(box=[25, 25, 200, 65], text='Original prose', reason='',
                lines=[dict(box=[30, 35, 150, 43], text='Original prose')],
                recovered=recovered, **fonts)


@pytest.mark.parametrize('family,name', [('simsun', 'SimSun'), ('msyh', 'Microsoft YaHei')])
@pytest.mark.parametrize('recovered', [False, True])
def test_selected_scan_font_draws_exact_mixed_text_within_box(family, name, recovered):
    if not Path('C:/Windows/Fonts/simsun.ttc').is_file():
        pytest.skip('Requires installed fonts; no downloads')
    with f.open() as doc:
        p = doc.new_page(width=240, height=120)
        r = region(recovered, chinese_font=family, latin_font='times')
        fitted = fit_translation(p, r, '中文 ABC 123', 'zh-Hans', JobControl())
        assert fitted and fitted[1]['size'] >= 6
        assert {face['family'] for face in fitted[1]['font_policy']['used_faces']} == {name, 'Times New Roman'}
        if recovered:
            assert fitted[1]['method'] == 'glyph_bounds'
        else:
            assert fitted[1]['spacing'] in (1.3, 1.2)
        with f.open(stream=fitted[0], filetype='pdf') as out:
            assert '中文 ABC 123' in out[0].get_text()
            pix = out[0].get_pixmap(matrix=f.Matrix(4,4), colorspace=f.csGRAY)
            y, x = np.nonzero(np.frombuffer(pix.samples, np.uint8).reshape(pix.height,pix.width)<240)
            assert x.min()/4 >= 25 and (x.max()+1)/4 <= 200
            assert y.min()/4 >= 25 and (y.max()+1)/4 <= 65


def test_empty_legacy_pair_keeps_original_font_path():
    with f.open() as doc:
        p = doc.new_page(width=240, height=120)
        outputs = [fit_translation(p, r, '中文测试', 'zh-Hans', JobControl()) for r in
                   (region(), region(chinese_font='', latin_font=''))]
        assert all(outputs)
        assert outputs[0][1] == outputs[1][1] and 'font_policy' not in outputs[0][1]
        with f.open(stream=outputs[0][0], filetype='pdf') as a, f.open(stream=outputs[1][0], filetype='pdf') as b:
            assert a[0].get_pixmap().samples == b[0].get_pixmap().samples


def test_selected_scan_overflow_never_modifies_source_or_shrinks_below_floor():
    with f.open() as doc:
        p = doc.new_page(width=240, height=120)
        before = p.get_pixmap().samples
        assert fit_translation(p, region(chinese_font='simsun', latin_font='times'),
                               '过长的完整译文' * 200, 'zh-Hans', JobControl()) is None
        assert p.get_pixmap().samples == before


def test_partial_scan_policy_raises_instead_of_builtin_fallback():
    with f.open() as doc:
        p = doc.new_page(width=240, height=120)
        with pytest.raises(DocumentError):
            fit_translation(p, region(chinese_font='simsun', latin_font=''),
                            '中文', 'zh-Hans', JobControl())


@pytest.mark.parametrize('translation,accepted', [('中文 ABC', True), ('\U0010ffff', False)])
def test_job_policy_reaches_scan_page_and_missing_glyph_preserves_source(translation, accepted):
    with f.open() as original, f.open() as scan:
        p = original.new_page(width=240, height=120)
        p.insert_text((30,45), 'Original prose', fontsize=10)
        p.draw_line((30,90), (200,90), color=(1,0,0))
        scan.new_page(width=240,height=120).insert_image(p.rect,pixmap=p.get_pixmap(matrix=f.Matrix(3,3)))
        box = list(p.search_for('Original prose')[0])
        r = dict(box=list(f.Rect(box)+(-3,-3,3,3)), text='Original prose', reason='',
                 lines=[dict(box=box,text='Original prose')], recovered=True)
        t = NS(control=JobControl(), job=NS(target='zh-Hans', chinese_font='simsun', latin_font='times'),
               translate=lambda text:translation)
        before=scan[0].get_pixmap().samples
        out, retained, placed = translate_scan_page(scan,0,'scan_image',NS(regions=lambda *a:[r]),'offline',t,lambda _:None)
        with out:
            if accepted:
                assert len(placed)==1 and not retained
                assert placed[0]['font_policy']['used_faces']
                assert translation in out[0].get_text()
                clip=f.Rect(25,85,205,95)
                # Reopen both PDFs to avoid MuPDF's cached image-subsampling
                # state from prior differently scaled eraser/probe renders.
                with f.open(stream=scan.tobytes(),filetype='pdf') as a, f.open(stream=out.tobytes(),filetype='pdf') as b:
                    assert a[0].get_pixmap(clip=clip,alpha=True).samples == b[0].get_pixmap(clip=clip,alpha=True).samples
            else:
                assert not placed and len(retained)==1
                assert 'U+10FFFF' in retained[0]['reason']
                assert out[0].get_pixmap().samples == before


def test_selected_scan_paragraph_preserves_blank_line_and_spacing():
    with f.open() as doc:
        p=doc.new_page(width=240,height=120)
        r=region(chinese_font='simsun',latin_font='times')
        r['box']=[25,20,200,100]
        fitted=fit_translation(p,r,'第一段 ABC\n\n第二段 XYZ','zh-Hans',JobControl())
        assert fitted and fitted[1]['spacing'] in (1.3,1.2)
        with f.open(stream=fitted[0],filetype='pdf') as out:
            blocks=out[0].get_text('dict')['blocks']
            lines=[l for b in blocks for l in b.get('lines',[])]
            assert len(lines)==2
            first,second=[l['spans'][0]['origin'][1] for l in lines]
            assert second-first >= fitted[1]['size']*2*1.2-.01
            assert '第一段 ABC' in out[0].get_text() and '第二段 XYZ' in out[0].get_text()


def test_mixed_scan_keeps_body_artifact_callback(tmp_path, monkeypatch):
    import linguafloat.document_graphics as graphics
    import linguafloat.document_postprocess as postprocess
    from linguafloat.document_scan_layout import ScanAnalyzer, translate_mixed_layout_pdf
    from test_document_core import job_for
    from test_document_strict_layout import scan_fixture
    source, detected = scan_fixture(tmp_path)
    job = job_for(source, workflow='body', chinese_font='simsun', latin_font='times')
    translator = NS(job=job, control=JobControl(), translate=lambda text:'中文 ABC')
    monkeypatch.setattr(ScanAnalyzer, 'regions', lambda *args:[detected])
    monkeypatch.setattr(graphics, 'DocumentEnhancer', lambda *args:NS(apply=lambda *a:None, report=lambda *a:None))
    captured=[]
    def save_body(output, job, mono, mapping, owner):
        captured.append((mono[0].get_text(), mapping, owner is translator))
    monkeypatch.setattr(postprocess, 'save_body_artifact', save_body)
    paths, warnings = translate_mixed_layout_pdf(job, translator, tmp_path, lambda _:None,
                                                [0], {0:'scan_ocr'})
    assert len(captured)==1 and captured[0][2]
    assert '中文 ABC' in captured[0][0]
    assert captured[0][1]==[dict(source=0,start=0,count=1,selected=True)]
    assert paths and all(Path(path).is_file() for path in paths)
