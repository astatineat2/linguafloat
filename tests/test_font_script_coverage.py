"""Offline regression for goji job 0ff43a233c414e8898452456da9481dc."""
import socket
from types import SimpleNamespace

import pytest

from linguafloat import document_fonts as fonts


# Exact excerpts of translated.json, page 3, paragraph o7A8F, parts 8/10.
# The paragraph box matches P3-U2; U+2236 occurs twice in each part.
GOJI_TEXT = (
    "27.6 mmol/L\u4e59\u9178\u94a0\u2236\u4e09\u4e59\u80fa\u2236"
    "\u56db\u6c22\u544b\u5583\uff08500:0.11:2.5\uff0cv/v/v\uff09"
    "80.9 mmol/L\u4e59\u9178\u94a0\u2236\u7532\u9187\u2236"
    "\u4e59\u8148\uff081:2:2\uff0cv/v/v\uff09"
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Font coverage tests must never use the network")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


@pytest.fixture(params=fonts.CHINESE_FONTS)
def resolver(request):
    pytest.importorskip("pymupdf")
    filename = "simsun.ttc" if request.param == "SimSun" else "msyh.ttc"
    if not (fonts._font_directory() / filename).is_file():
        pytest.skip("Requires installed Windows fonts")
    return fonts.FitzFontResolver(request.param, fonts.DEFAULT_LATIN)


@pytest.mark.parametrize("bold,italic", [(False, False), (True, False), (False, True), (True, True)])
def test_ratio_uses_selected_chinese_face_with_original_style(resolver, bold, italic):
    face = resolver.resolve("\u2236", bold=bold, italic=italic)
    assert face.source.family == resolver.policy.chinese
    assert face.has_glyph(0x2236)
    assert face.is_bold == bold and face.is_italic == italic
    assert face.synthetic_bold == (bold and resolver.policy.chinese == "SimSun")
    assert face.synthetic_italic == italic
    for char in "Abc123:;\u03bc\u03a9\u00b1\u2212":
        latin = resolver.resolve(char, bold=bold, italic=italic)
        assert latin.source.family == fonts.DEFAULT_LATIN
        assert latin.source.bold == bold and latin.source.italic == italic


def test_missing_latin_glyph_does_not_fall_back_to_selected_chinese(resolver, monkeypatch):
    assert resolver.face(resolver.policy.chinese).has_glyph(ord("A"))
    monkeypatch.setattr(resolver.face(resolver.policy.latin), "has_glyph", lambda *a, **k: 0)
    with pytest.raises(fonts.FontPolicyError, match=r"Times New Roman.*U\+0041"):
        resolver.resolve("A")
    with pytest.raises(fonts.FontPolicyError, match=r"U\+10FFFF"):
        resolver.resolve("\U0010ffff")


def test_missing_ratio_in_selected_chinese_face_still_fails(resolver, monkeypatch):
    monkeypatch.setattr(resolver.face(resolver.policy.chinese), "has_glyph", lambda *a, **k: 0)
    with pytest.raises(fonts.FontPolicyError, match=r"U\+2236") as error:
        resolver.resolve("\u2236")
    assert resolver.policy.chinese in str(error.value)


@pytest.mark.parametrize("bold,italic", [(False, False), (True, False), (False, True), (True, True)])
def test_goji_ratio_typesetting_and_pdf_roundtrip(resolver, tmp_path, bold, italic):
    fitz = pytest.importorskip("pymupdf")
    pytest.importorskip("babeldoc")
    from bitstring import BitStream
    from babeldoc.format.pdf.document_il import (
        Document, Page, PdfFont, PdfParagraph, PdfParagraphComposition,
        PdfSameStyleUnicodeCharacters, PdfStyle,
    )
    from babeldoc.format.pdf.document_il.backend.pdf_creater import (
        CharacterRenderUnit, PDFCreater, RenderContext,
    )
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
    from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper

    class Monitor:
        def stage_start(self, *args):
            from contextlib import nullcontext
            return nullcontext(SimpleNamespace(advance=lambda *a: None))

    original = PdfFont(font_id="F0", bold=bold, italic=italic, serif=True)
    style = PdfStyle(font_id="F0", font_size=12)
    paragraph = PdfParagraph(pdf_style=style, xobj_id=0, pdf_paragraph_composition=[
        PdfParagraphComposition(pdf_same_style_unicode_characters=
            PdfSameStyleUnicodeCharacters(pdf_style=style, unicode=GOJI_TEXT))])
    output = tmp_path / "ratio.pdf"
    with fonts.font_policy(resolver.policy.chinese, fonts.DEFAULT_LATIN) as policy, fitz.open() as pdf:
        mapper = FontMapper(SimpleNamespace(progress_monitor=Monitor()))
        assert mapper.has_char("\u2236")
        engine = object.__new__(Typesetting)
        engine.font_mapper = mapper
        units = engine.create_typesetting_units(paragraph, {"F0": original})
        assert "".join(unit.unicode for unit in units) == GOJI_TEXT
        ratios = [unit for unit in units if unit.unicode == "\u2236"]
        assert len(ratios) == 4
        assert all(unit.font.source.family == resolver.policy.chinese for unit in ratios)
        pdf.new_page(width=900, height=120)
        page = Page(page_number=0)
        x = 20
        for unit in units:
            characters, _, _ = unit.relocate(x, 60, 1).render()
            page.pdf_character.extend(characters)
            x += unit.width
        mapper.add_font(pdf, Document(page=[page], total_pages=1))
        encodings = {font.font_id: font.encoding_length for font in page.pdf_font}
        context = RenderContext(object.__new__(PDFCreater), page, set(encodings), encodings,
                                encodings, {}, {}, b"", check_font_exists=True)
        stream = BitStream()
        for char in page.pdf_character:
            CharacterRenderUnit(char, 0).render(stream, context)
        xref = pdf.get_new_xref()
        pdf.update_object(xref, "<<>>")
        pdf.update_stream(xref, stream.tobytes())
        pdf[0].set_contents(xref)
        pdf.subset_fonts(fallback=False)
        pdf.save(output)
        assert {face["family"] for face in policy.report()["used_faces"]} == {
            resolver.policy.chinese, fonts.DEFAULT_LATIN}
    with fitz.open(output) as pdf:
        assert pdf[0].get_text().rstrip("\n") == GOJI_TEXT
        pix = pdf[0].get_pixmap(colorspace=fitz.csGRAY)
        assert min(pix.samples) < 255


def test_ratio_routing_invalidates_old_render_key_only():
    assert fonts.font_policy_key("msyh", "times") != (
        "windows-fonts-v1:Microsoft YaHei:Times New Roman")
    assert fonts.font_policy_key("", "") == ""
