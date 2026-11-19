"""Overlay glyphs must survive the active body-engine font adapter."""
import pymupdf as f
import pytest

from linguafloat.document_fonts import FitzFontResolver


@pytest.mark.parametrize('family', ['msyh', 'simsun'])
def test_overlay_chinese_survives_pdf_composition(family):
    value = '\u6536\u7a3f\u65e5\u671f\uff1a2025\u5e743\u670816\u65e5'
    with f.open() as overlay, f.open() as final:
        page = overlay.new_page()
        resolver = FitzFontResolver(family, 'times')
        resolver.draw_line(page, (40, 80), value, fontsize=9, bold=True)
        assert value in page.get_text()
        target = final.new_page()
        target.show_pdf_page(target.rect, overlay, 0)
        with f.open(stream=final.tobytes(), filetype='pdf') as reopened:
            assert value in reopened[0].get_text()


@pytest.mark.parametrize('family', ['msyh', 'simsun'])
def test_overlay_chinese_inside_body_policy(family):
    from linguafloat.document_core import JobControl
    from linguafloat.document_layout import LayoutPolicy
    from linguafloat.document_fonts import BabelDOCFontPolicy
    with BabelDOCFontPolicy(family, 'times'), LayoutPolicy(JobControl()):
        test_overlay_chinese_survives_pdf_composition(family)
