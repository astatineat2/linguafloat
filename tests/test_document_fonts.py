"""Offline, actual installed-font/mapper/rendering checks; no font redistribution."""
from contextlib import contextmanager
import copy
import json
import re
import socket
import struct
from types import SimpleNamespace

import pytest

from linguafloat import document_fonts as fonts


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Font policy tests must never use the network")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)


def test_keys_are_layout_only_canonical_and_legacy_is_empty():
    assert fonts.font_policy_key(None, None) == ""
    assert fonts.font_policy_key("", "") == ""
    assert fonts.font_policy_key("宋体", "times new roman") == fonts.font_policy_key("SimSun", "Times New Roman")
    assert fonts.font_policy_key("微软雅黑", "Times New Roman") != fonts.font_policy_key("SimSun", "Times New Roman")
    assert fonts.DEFAULT_CHINESE == "SimSun"
    for chinese, latin in [("SimSun", ""), ("", "Times New Roman"), ("Arial", "Arial")]:
        with pytest.raises(fonts.FontPolicyError):
            fonts.font_policy_key(chinese, latin)


def test_empty_policy_never_checks_disk_or_imports_pdf_runtime(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("legacy policy must not inspect fonts")
    monkeypatch.setattr(fonts, "_font_directory", forbidden)
    assert not fonts.validate_fonts("", "").enabled
    with fonts.font_policy("", "") as policy:
        assert policy.report() == {"key": "", "warnings": [], "used_faces": []}


def test_missing_font_is_explicit_and_context_can_be_entered_after_failure(tmp_path):
    with pytest.raises(fonts.FontPolicyError, match="times.ttf"):
        fonts.validate_fonts("SimSun", "Times New Roman", fonts_dir=tmp_path)
    with pytest.raises(fonts.FontPolicyError):
        with fonts.font_policy("SimSun", "Times New Roman", fonts_dir=tmp_path):
            pytest.fail("should not enter")
    with fonts.font_policy():
        pass


def sfnt_metadata(path, family="Times New Roman", fs_type=8, bold=False, italic=False):
    """Tiny metadata-only fixture: not a redistributed or renderable font."""
    name = family.encode("utf-16-be")
    names = struct.pack(">HHH", 0, 1, 18) + struct.pack(">HHHHHH", 3, 1, 0x409, 1, len(name), 0) + name
    os2 = bytearray(64)
    struct.pack_into(">H", os2, 8, fs_type)
    struct.pack_into(">H", os2, 62, 32 * bold + int(italic))
    header = struct.pack(">IHHHH", 0x10000, 2, 0, 0, 0)
    directory = struct.pack(">4sIII", b"OS/2", 0, 44, len(os2))
    directory += struct.pack(">4sIII", b"name", 0, 44 + len(os2), len(names))
    path.write_bytes(header + directory + os2 + names)


@pytest.mark.parametrize("fs_type", [2, 4, 0x108, 0x208])
def test_embedding_restrictions_are_checked_without_pdf_dependencies(tmp_path, fs_type):
    sfnt_metadata(tmp_path / "times.ttf", fs_type=fs_type)
    with pytest.raises(fonts.FontPolicyError, match="fsType"):
        fonts.validate_fonts("SimSun", "Times New Roman", fonts_dir=tmp_path)


def test_wrong_family_style_and_corrupt_font_are_explicit(tmp_path):
    path = tmp_path / "times.ttf"
    for family, bold in [("Wrong Family", False), ("Times New Roman", True)]:
        sfnt_metadata(path, family=family, bold=bold)
        with pytest.raises(fonts.FontPolicyError, match="不符"):
            fonts.validate_fonts("SimSun", "Times New Roman", fonts_dir=tmp_path)
    path.write_bytes(b"bad font")
    with pytest.raises(fonts.FontPolicyError, match="无法读取"):
        fonts.validate_fonts("SimSun", "Times New Roman", fonts_dir=tmp_path)


@pytest.mark.parametrize("family", fonts.CHINESE_FONTS)
def test_actual_windows_font_validation_in_app_runtime(family):
    if not (fonts._font_directory() / "times.ttf").exists():
        pytest.skip("Windows fonts not installed")
    validated = fonts.validate_fonts(family, fonts.DEFAULT_LATIN)
    assert validated.enabled and validated.warnings
    assert len(validated.files) == (5 if family == "SimSun" else 6)
    assert all(f.fs_type in (0, 8) for f in validated.files)


@pytest.fixture
def pdf_runtime(monkeypatch):
    fitz = pytest.importorskip("pymupdf")
    pytest.importorskip("babeldoc")
    if not (fonts._font_directory() / "simsun.ttc").exists():
        pytest.skip("Windows font rendering requires installed Windows fonts")
    from babeldoc.assets import assets
    def no_assets(*args, **kwargs):
        raise AssertionError("Explicit font policy must not request BabelDOC font assets")
    monkeypatch.setattr(assets, "get_font_family", no_assets)
    monkeypatch.setattr(assets, "get_font_and_metadata", no_assets)
    return fitz


def test_installed_mapper_reproduces_wenkai_bold_loss_then_policy_fixes(pdf_runtime):
    from babeldoc.format.pdf.document_il import PdfFont
    from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper, PrimaryFontFamily
    mapper = object.__new__(FontMapper)
    mapper.primary_font_family = PrimaryFontFamily.NONE
    wenkai = SimpleNamespace(font_id="LXGWWenKaiGB-Regular", is_bold=False, has_glyph=lambda _: True)
    bold = SimpleNamespace(font_id="SourceHanSerifCN-Bold", is_bold=True, has_glyph=lambda _: True)
    mapper.script_fonts, mapper.normal_fonts, mapper.fallback_fonts = [wenkai], [bold], [bold]
    mapper.type2font = {"script": [wenkai], "normal": [bold], "fallback": [bold]}
    original = PdfFont(font_id="F1", bold=True, italic=True, serif=True)
    assert mapper.map(original, "植") is wenkai  # real installed buggy method
    with fonts.font_policy("SimSun", "Times New Roman") as policy:
        adapted = FontMapper(SimpleNamespace())
        selected = adapted.map(original, "植")
        assert selected.source.family == "SimSun"
        assert selected.is_bold and selected.is_italic
        assert selected.synthetic_bold and selected.synthetic_italic
        latin = adapted.map(original, "A")
        assert latin.source.path.name == "timesbi.ttf"
        assert not latin.synthetic_bold and not latin.synthetic_italic
        assert len(policy.report()["used_faces"]) == 2
        assert mapper.map(original, "植") is wenkai  # pre-existing mapper unaffected


def test_mapper_and_renderer_restore_on_success_exception_and_nested_rejection(pdf_runtime):
    from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper
    from babeldoc.format.pdf.document_il.backend.pdf_creater import CharacterRenderUnit
    before = (FontMapper.__init__, FontMapper.map, FontMapper.add_font, CharacterRenderUnit.render)
    for fail in (False, True):
        try:
            with fonts.font_policy("SimSun", "Times New Roman"):
                assert FontMapper.map is not before[1]
                with pytest.raises(fonts.FontPolicyError, match="不可重叠"):
                    with fonts.font_policy("Microsoft YaHei", "Times New Roman"):
                        pass
                if fail:
                    raise RuntimeError("deliberate conversion failure")
        except RuntimeError:
            assert fail
        assert (FontMapper.__init__, FontMapper.map, FontMapper.add_font, CharacterRenderUnit.render) == before


def test_resolver_strict_coverage_and_true_style_faces(pdf_runtime):
    resolver = fonts.FitzFontResolver("Microsoft YaHei", "Times New Roman")
    assert [(text, f.source.family) for text, f in resolver.runs("中文，Café 123") ] == [
        ("中文，", "Microsoft YaHei"), ("Café 123", "Times New Roman")]
    for bold, italic, name in [(False, False, "times.ttf"), (True, False, "timesbd.ttf"),
                               (False, True, "timesi.ttf"), (True, True, "timesbi.ttf")]:
        assert resolver.resolve("é", bold=bold, italic=italic).source.path.name == name
    face = resolver.resolve("中", bold=True, italic=True)
    assert face.source.path.name == "msyhbd.ttc"
    assert not face.synthetic_bold and face.synthetic_italic
    assert face.ink_padding(20)[2] > 0
    with pytest.raises(fonts.FontPolicyError, match=r"U\+10FFFF"):
        resolver.resolve("\U0010ffff")
    with pytest.raises(fonts.FontPolicyError, match="缺少字形"):
        resolver.resolve("A").text_length("中文")


class Monitor:
    @contextmanager
    def stage_start(self, *args):
        yield SimpleNamespace(advance=lambda *args: None)


def render_context(page):
    from babeldoc.format.pdf.document_il.backend.pdf_creater import RenderContext, PDFCreater
    encodings = {f.font_id: f.encoding_length for f in page.pdf_font}
    return RenderContext(object.__new__(PDFCreater), page, set(encodings), encodings, encodings,
                         {}, {}, b"", check_font_exists=True)


@pytest.mark.parametrize("family", fonts.CHINESE_FONTS)
@pytest.mark.parametrize('named_color', [False, True])
def test_real_typesetting_embedding_encoding_and_synthetic_rendering(pdf_runtime, tmp_path, family, named_color):
    fitz = pdf_runtime
    from bitstring import BitStream
    from babeldoc.format.pdf.document_il import Document, Page, PdfFont, PdfStyle, GraphicState
    from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
    from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper
    from babeldoc.format.pdf.document_il.backend.pdf_creater import CharacterRenderUnit
    with fonts.font_policy(family, "Times New Roman") as policy, fitz.open() as pdf:
        pdf.new_page(width=500, height=340)
        if named_color:
            resource = int(pdf.xref_get_key(pdf[0].xref, 'Resources')[1].split()[0])
            pdf.xref_set_key(resource, 'ColorSpace', '<</LFColor /DeviceRGB>>')
        mapper = FontMapper(SimpleNamespace(progress_monitor=Monitor()))
        page = Page(page_number=0)
        for row, (bold, italic) in enumerate([(False, False), (True, False), (False, True), (True, True)]):
            original = PdfFont(font_id="Source", bold=bold, italic=italic, serif=True)
            x = 40
            for char in "植物菌名 Abc123":
                face = mapper.map(original, char)
                unit = TypesettingUnit(unicode=char, font=face, original_font=original, font_size=24,
                    style=PdfStyle(font_id="Source", font_size=24,
                                   graphic_state=GraphicState(passthrough_per_char_instruction=
                                       "/LFColor cs 0.1 0.2 0.3 scn" if named_color else "0.1 0.2 0.3 rg")),
                    xobj_id=0).relocate(x, 280-row*65, 1)
                characters, _, _ = unit.render()
                page.pdf_character.extend(characters)
                x += unit.width
        document = Document(page=[page], total_pages=1)
        mapper.add_font(pdf, document)  # Actual installed embedding path, including TTC.
        assert all(f.encoding_length == 2 for f in page.pdf_font)
        stream = BitStream()
        for char in page.pdf_character:
            CharacterRenderUnit(char, 0).render(stream, render_context(page))
        xref = pdf.get_new_xref()
        pdf.update_object(xref, "<<>>")
        pdf.update_stream(xref, stream.tobytes())
        pdf[0].set_contents(xref)
        pdf.subset_fonts(fallback=False)  # Same subset operation used by production.
        output = tmp_path / "font-policy.pdf"
        pdf.save(output)
        (tmp_path / "font-report.json").write_text(json.dumps(policy.report(), ensure_ascii=False, indent=2), encoding="utf-8")
    with fitz.open(output) as reopened:
        page = reopened[0]
        text = page.get_text()
        assert text.count("植物菌名 Abc123") == 4
        page.get_pixmap(matrix=fitz.Matrix(2, 2)).save(tmp_path / "font-policy.png")
        # Equal CJK strings at identical x positions: bold must add actual ink,
        # and oblique must change the raster, not just report bold/italic flags.
        images = []
        for row in range(4):
            y = 60 + row*65
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), colorspace=fitz.csGRAY,
                                 clip=fitz.Rect(32, y-28, 145, y+8), alpha=False)
            images.append(pix.samples)
        ink = [sum(255-v for v in image) for image in images]
        assert ink[1] > ink[0] * 1.03
        assert ink[3] > ink[2] * 1.03
        assert images[0] != images[2]
        spans = [s for b in page.get_text("dict")["blocks"] for line in b.get("lines", []) for s in line["spans"]]
        latin_fonts = {s["font"] for s in spans if "Abc123" in s["text"]}
        assert len(latin_fonts) == 4
        assert all("Times" in name for name in latin_fonts)
        assert all(abs(s["size"]-24) < .01 for s in spans)
        assert all(s["color"] == spans[0]["color"] for s in spans)
        assert all(abs(((spans[0]["color"] >> shift) & 255) - expected) <= 1
                   for shift, expected in [(16, 25.5), (8, 51), (0, 76.5)])


def test_original_formula_and_protected_glyph_rendering_are_byte_identical(pdf_runtime):
    from bitstring import BitStream
    from babeldoc.format.pdf.document_il import Box, Page, PdfFont, PdfStyle, PdfCharacter, PdfFormula
    from babeldoc.format.pdf.document_il.backend.pdf_creater import CharacterRenderUnit
    from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
    char = PdfCharacter(char_unicode="x", pdf_character_id=91, box=Box(30, 50, 40, 60),
                        pdf_style=PdfStyle(font_id="OriginalMath", font_size=10), xobj_id=0)
    formula = PdfFormula(pdf_character=[char], box=char.box, x_offset=0, y_offset=0)
    saved = copy.deepcopy(formula)
    page = Page(page_number=0, pdf_font=[PdfFont(font_id="OriginalMath", encoding_length=2)])
    baseline = BitStream()
    CharacterRenderUnit(char, 0).render(baseline, render_context(page))
    with fonts.font_policy("SimSun", "Times New Roman"):
        chars, _, _ = TypesettingUnit(formular=formula).render()
        assert chars[0] is char
        rendered = BitStream()
        CharacterRenderUnit(char, 0).render(rendered, render_context(page))
        assert rendered == baseline
        assert formula == saved


def test_overlay_resolver_produces_searchable_mixed_script_pdf(pdf_runtime, tmp_path):
    fitz = pdf_runtime
    resolver = fonts.FitzFontResolver()
    with fitz.open() as pdf:
        page = pdf.new_page()
        x = 40
        for text, face in resolver.runs("植物 Abc", bold=True, italic=True):
            point = (x, 80)
            face.insert_text(page, point, text, color=(.2, .2, .2), fontsize=24)
            x += face.text_length(text, fontsize=24)
        assert resolver.draw_line(page, (40, 130), "样本 A B\u00a0μ−2", fontsize=18) > 0
        path = tmp_path / "overlay.pdf"
        pdf.save(path)
    with fitz.open(path) as pdf:
        assert "植物 Abc" in pdf[0].get_text()
        assert "样本 A B\u00a0μ−2" in pdf[0].get_text()
        assert b"2 Tr" in pdf[0].read_contents()
        assert re.search(rb"(?:0)?\.6 w", pdf[0].read_contents())
        pdf[0].get_pixmap().save(tmp_path / "overlay.png")


def test_layout_policy_composes_with_font_adapter_and_preserves_protected_parts(pdf_runtime):
    from babeldoc.format.pdf.document_il import (Box, PdfFont, PdfStyle, PdfParagraph, PdfCharacter,
        PdfFormula, PdfParagraphComposition, PdfSameStyleUnicodeCharacters)
    from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
    from linguafloat.document_layout import LayoutPolicy
    from linguafloat.document_core import JobControl
    style = PdfStyle(font_id="Original", font_size=17)
    char = PdfCharacter(char_unicode="x", pdf_character_id=91, box=Box(10, 10, 20, 20), pdf_style=style)
    formula = PdfFormula(box=char.box, pdf_character=[char], x_offset=0, y_offset=0)
    paragraph = PdfParagraph(unicode="植A", pdf_style=style, xobj_id=-1, debug_id="test", pdf_paragraph_composition=[
        PdfParagraphComposition(pdf_character=char), PdfParagraphComposition(pdf_formula=formula),
        PdfParagraphComposition(pdf_same_style_unicode_characters=PdfSameStyleUnicodeCharacters(unicode="植A", pdf_style=style))])
    original = PdfFont(font_id="Original", bold=True, italic=True)
    saved_add = FontMapper.add_font
    with fonts.font_policy("SimSun", "Times New Roman"), LayoutPolicy(JobControl()):
        engine = object.__new__(Typesetting)
        engine.font_mapper = FontMapper(SimpleNamespace())
        units = engine.create_typesetting_units(paragraph, {"Original": original})
        assert units[0].char is char and units[1].formular is formula
        assert units[2].font.source.family == "SimSun"
        assert units[2].font.synthetic_bold and units[2].font.synthetic_italic
        assert units[3].font.source.path.name == "timesbi.ttf"
    assert FontMapper.add_font is saved_add
