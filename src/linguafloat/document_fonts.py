"""Local Windows font policy for *translated* text, never original PDF glyphs.

New jobs explicitly pass DEFAULT_CHINESE / DEFAULT_LATIN. Empty pairs are a
legacy no-op (including the cache key); callers must not fill in old snapshots.
Validation uses only the standard library, so it also runs in the GUI runtime.

Use ``with BabelDOCFontPolicy(chinese, latin) as fonts:`` around construction,
typesetting AND PDF writing of every BabelDOC object in a worker process. Enter
this before LayoutPolicy and exit it afterwards. The adapter is process-global,
reversible and deliberately rejects overlapping/nested contexts. Do not run an
unwrapped translation concurrently in that process. Keep formulas / protected
original glyphs in BabelDOC's PdfFormula / PdfCharacter passthrough path; never
convert them to translated Unicode merely to apply this policy.

No font files are copied or modified. Synthesis is PDF fill-and-stroke (0.025 em)
and a 12-degree text shear, not a different CJK face. Advance widths stay those
of the real face; layout must allow the reported ink padding. PDF font metadata
still describes the actual embedded face, so synthetic style is also reported
explicitly. This is a targeted adapter for the installed BabelDOC API, not a
general replacement font engine.

The SimSun stroke adapter mirrors device and named fill colour operators in
their original graphics-state scope; unsupported syntax fails explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
import os
from pathlib import Path
import re
import struct
import threading
import unicodedata

DEFAULT_CHINESE = "SimSun"
DEFAULT_LATIN = "Times New Roman"
CHINESE_FONTS = ("SimSun", "Microsoft YaHei")
LATIN_FONTS = (DEFAULT_LATIN,)
_VERSION = "windows-fonts-v2"
_STROKE_EM = .025
_SHEAR = math.tan(math.radians(12))
_PATCH_LOCK = threading.Lock()


class FontPolicyError(ValueError):
    """Actionable policy / installation / embedding / glyph coverage failure."""


def _names(chinese, latin):
    chinese, latin = (chinese or "").strip(), (latin or "").strip()
    if not chinese and not latin:
        return "", ""
    aliases = {"宋体": "SimSun", "simsun": "SimSun",
                   "微软雅黑": "Microsoft YaHei", "msyh": "Microsoft YaHei", "microsoft yahei": "Microsoft YaHei"}
    chinese = aliases.get(chinese.casefold(), chinese)
    latin = DEFAULT_LATIN if latin.casefold() in {"times", DEFAULT_LATIN.casefold()} else latin
    if chinese not in CHINESE_FONTS or latin not in LATIN_FONTS:
        raise FontPolicyError("请选择宋体或微软雅黑，并同时选择 Times New Roman；"
                              "仅两个字体均为空时允许历史字体策略。")
    return chinese, latin


def font_policy_key(chinese="", latin="") -> str:
    """Stable layout-only key; empty means omit the field from legacy keys.

    This versions the mapping/synthesis algorithm, not installed font binaries.
    Do not add this to translation request/cache keys.
    """
    chinese, latin = _names(chinese, latin)
    return f"{_VERSION}:{chinese}:{latin}" if chinese else ""


@dataclass(frozen=True)
class FontFile:
    path: Path
    family: str
    bold: bool
    italic: bool
    fs_type: int
    face_index: int = 0


@dataclass(frozen=True)
class ValidatedFonts:
    chinese: str
    latin: str
    files: tuple[FontFile, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def enabled(self):
        return bool(self.chinese)

    @property
    def key(self):
        return font_policy_key(self.chinese, self.latin)


def _font_directory() -> Path:
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


def _metadata(path: Path) -> tuple[set[str], bool, bool, int]:
    """Read first SFNT/TTC face, the same face selected by MuPDF's file loader."""
    try:
        with path.open("rb") as stream:
            length = stream.seek(0, 2)

            def read(offset, count):
                if offset < 0 or offset + count > length:
                    raise ValueError("truncated font table")
                stream.seek(offset)
                data = stream.read(count)
                if len(data) != count:
                    raise ValueError("truncated font read")
                return data

            offset = 0
            if read(0, 4) == b"ttcf":
                if struct.unpack(">I", read(8, 4))[0] < 1:
                    raise ValueError("empty collection")
                offset = struct.unpack(">I", read(12, 4))[0]
            if read(offset, 4) not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
                raise ValueError("not an SFNT font")
            count = struct.unpack(">H", read(offset + 4, 2))[0]
            tables = {}
            for i in range(count):
                tag, _, start, size = struct.unpack(">4sIII", read(offset + 12 + 16*i, 16))
                if start + size > length:
                    raise ValueError("invalid font table bounds")
                tables[tag] = (start, size)
            os2_start, os2_size = tables[b"OS/2"]
            if os2_size < 64:
                raise ValueError("missing embedding/style metadata")
            fs_type = struct.unpack(">H", read(os2_start + 8, 2))[0]
            selection = struct.unpack(">H", read(os2_start + 62, 2))[0]
            name_start, name_size = tables[b"name"]
            names = read(name_start, name_size)
            _, name_count, storage = struct.unpack_from(">HHH", names)
            families = set()
            for i in range(name_count):
                platform, _, _, name_id, size, start = struct.unpack_from(">HHHHHH", names, 6 + 12*i)
                if name_id not in (1, 16):
                    continue
                if storage + start + size > len(names):
                    raise ValueError("invalid family name")
                value = names[storage + start:storage + start + size]
                if platform in (0, 3):
                    families.add(value.decode("utf-16-be"))
                elif platform == 1:
                    families.add(value.decode("mac_roman"))
            return families, bool(selection & 32), bool(selection & 1), fs_type
    except (OSError, ValueError, KeyError, struct.error) as error:
        raise FontPolicyError(f"无法读取本机字体 {path}：{error}。请修复 Windows 字体安装。") from error


def validate_fonts(chinese="", latin="", *, fonts_dir=None) -> ValidatedFonts:
    """Validate installed first faces and editable embedding, without PDF imports.

    ``fonts_dir`` is an optional local Windows-font directory for testing/custom
    Windows locations. Missing styles never silently fall back to another family.
    No-subsetting fonts are rejected because the existing PDF writer subsets.
    """
    chinese, latin = _names(chinese, latin)
    if not chinese:
        return ValidatedFonts("", "")
    root = Path(fonts_dir) if fonts_dir is not None else _font_directory()
    required = [("times.ttf", latin, False, False), ("timesbd.ttf", latin, True, False),
                ("timesi.ttf", latin, False, True), ("timesbi.ttf", latin, True, True)]
    required += ([("simsun.ttc", chinese, False, False)] if chinese == "SimSun" else
                 [("msyh.ttc", chinese, False, False), ("msyhbd.ttc", chinese, True, False)])
    files = []
    for filename, family, bold, italic in required:
        path = root / filename
        if not path.is_file():
            raise FontPolicyError(f"缺少本机字体 {family}（{path}）。请安装/修复 Windows 字体后重试；不会自动替换字体。")
        names, actual_bold, actual_italic, fs_type = _metadata(path)
        if family.casefold() not in {name.casefold() for name in names} or (bold, italic) != (actual_bold, actual_italic):
            raise FontPolicyError(f"字体文件与所选字体/样式不符：{path}，实际字体 {sorted(names)}。")
        if fs_type & (2 | 0x100 | 0x200) or (fs_type & 4 and not fs_type & 8):
            raise FontPolicyError(f"字体 {path} 的嵌入权限 fsType={fs_type} 不支持当前可编辑/子集 PDF 输出。")
        files.append(FontFile(path, family, bold, italic, fs_type))
    warning = ("宋体缺少粗体/斜体字面：粗体使用 0.025 em 描边，斜体使用 12° 倾斜；保留宋体字形。"
               if chinese == "SimSun" else
               "微软雅黑粗体使用真实粗体；缺少斜体字面，斜体使用 12° 倾斜。")
    return ValidatedFonts(chinese, latin, tuple(files), (warning,))


def _is_cjk(char: str) -> bool:
    point = ord(char)
    # RATIO is neutral-width: route it explicitly to the selected Chinese face.
    # Times lacks it; preserve U+2236 and still reject missing glyphs there.
    return (point == 0x2236 or unicodedata.east_asian_width(char) in ("W", "F") or
            0x2E80 <= point <= 0xA4CF or 0xAC00 <= point <= 0xD7AF or
            0xF900 <= point <= 0xFAFF or 0x20000 <= point <= 0x323AF)


@dataclass
class ResolvedFont:
    """Fitz font plus explicit drawing requirements; do not discard synthesis.

    For overlays: measure with ``text_length`` and draw with ``insert_text``.
    Mixed labels must be split into ``resolver.runs(text)`` to keep Latin in
    Times New Roman, or drawn with ``resolver.draw_line``.
    """
    font: object
    source: FontFile
    is_bold: bool
    is_italic: bool
    font_id: str
    synthetic_bold: bool = False
    synthetic_italic: bool = False

    def __getattr__(self, name):
        return getattr(self.font, name)

    @property
    def font_path(self):
        return self.source.path

    @property
    def encoding_length(self):
        return 2  # Identity-H CID/GID, verified with the installed MuPDF writer.

    @property
    def ascent_fontmap(self):
        return self.font.ascender * 1000

    @property
    def descent_fontmap(self):
        return self.font.descender * 1000

    def has_glyph(self, codepoint, **kwargs):
        return self.font.has_glyph(codepoint, fallback=0)

    def check_text(self, text):
        for char in text:
            if not self.has_glyph(ord(char)):
                raise FontPolicyError(f"所选字体 {self.source.family} 缺少字形 U+{ord(char):04X} ({char!r})；不会自动替换字体。")

    def text_length(self, text, fontsize=11, **kwargs):
        self.check_text(text)
        return self.font.text_length(text, fontsize=fontsize, **kwargs)

    def char_lengths(self, text, fontsize=11, **kwargs):
        self.check_text(text)
        return self.font.char_lengths(text, fontsize=fontsize, **kwargs)

    def ink_padding(self, fontsize):
        """Conservative (left, top, right, bottom) extra ink beyond real metrics."""
        stroke = fontsize * _STROKE_EM / 2 if self.synthetic_bold else 0
        left = max(0, -self.font.descender) * fontsize * _SHEAR if self.synthetic_italic else 0
        right = max(0, self.font.ascender) * fontsize * _SHEAR if self.synthetic_italic else 0
        return (stroke + left, stroke, stroke + right, stroke)

    def insert_text_kwargs(self, fontsize, origin):
        """Low-level fitz parameters; use insert_text() for exact ActualText too."""
        import pymupdf
        result = dict(fontname=self.font_id, fontsize=fontsize,
                      render_mode=2 if self.synthetic_bold else 0,
                      border_width=_STROKE_EM if self.synthetic_bold else 0)
        if self.synthetic_italic:
            result["morph"] = (pymupdf.Point(origin), pymupdf.Matrix(1, 0, _SHEAR, 1, 0, 0))
        return result

    def insert_text(self, page, origin, text, *, fontsize=11, color=(0, 0, 0)):
        """Draw one validated run and preserve Unicode despite cmap aliases.

        MuPDF maps the shared Times space/NBSP glyph back to NBSP. ActualText
        records the requested Unicode without modifying the installed font.
        """
        self.check_text(text)
        if not text:
            return
        if any(char in text for char in "\r\n\t"):
            raise FontPolicyError("覆盖层 insert_text 仅接受单行文本。")
        page.insert_font(fontname=self.font_id, fontbuffer=self.font.buffer)
        previous = set(page.get_contents())
        page.insert_text(origin, text, color=color, fill=color,
                         **self.insert_text_kwargs(fontsize, origin))
        for xref in set(page.get_contents()) - previous:
            data = page.parent.xref_stream(xref)
            # MuPDF can also add q/Q wrapper streams when wrapping the existing
            # page. Only the new text stream gets an accessibility replacement.
            if ("/" + self.font_id + " ").encode() in data:
                page.parent.update_stream(xref, _actual_text(data, text))


def _actual_text(data: bytes, text: str) -> bytes:
    value = (b"\xfe\xff" + text.encode("utf-16-be")).hex().upper().encode("ascii")
    return b"/Span <</ActualText <" + value + b">>> BDC\n" + data + b"\nEMC\n"


class FitzFontResolver:
    """Worker-side resolver with real faces, strict coverage and usage reporting."""
    def __init__(self, chinese=DEFAULT_CHINESE, latin=DEFAULT_LATIN, *, fonts_dir=None):
        self.policy = validate_fonts(chinese, latin, fonts_dir=fonts_dir)
        if not self.policy.enabled:
            raise FontPolicyError("历史空策略应继续使用原有字体解析器。")
        import pymupdf
        self._fonts = {f.path: pymupdf.Font(fontfile=str(f.path)) for f in self.policy.files}
        self._faces = {}
        self._used = set()

    def face(self, family, bold=False, italic=False):
        key = family, bool(bold), bool(italic)
        if key not in self._faces:
            if family not in (self.policy.chinese, self.policy.latin):
                raise FontPolicyError(f"字体未包含在当前策略中：{family}")
            actual_bold = bool(bold) and family != "SimSun"
            actual_italic = bool(italic) and family == self.policy.latin
            source = next(f for f in self.policy.files if
                          (f.family, f.bold, f.italic) == (family, actual_bold, actual_italic))
            tag = "CJK" if family == self.policy.chinese else "Latin"
            family_tag = "SimSun" if family == "SimSun" else "YaHei" if tag == "CJK" else "Times"
            self._faces[key] = ResolvedFont(self._fonts[source.path], source, bool(bold), bool(italic),
                f"LFLocal{family_tag}{tag}{int(bool(bold))}{int(bool(italic))}",
                bool(bold) and not actual_bold, bool(italic) and not actual_italic)
        return self._faces[key]

    def resolve(self, char, *, bold=False, italic=False):
        if len(char) != 1:
            raise FontPolicyError("字体解析需要单个 Unicode 字符；混合文本请使用 runs()。")
        face = self.face(self.policy.chinese if _is_cjk(char) else self.policy.latin, bold, italic)
        face.check_text(char)
        self._used.add(face.font_id)
        return face

    def runs(self, text, *, bold=False, italic=False):
        """Return [(substring, ResolvedFont), ...]; input is one line, no controls."""
        runs = []
        for char in text:
            face = self.resolve(char, bold=bold, italic=italic)
            if runs and runs[-1][1] is face:
                runs[-1] = (runs[-1][0] + char, face)
            else:
                runs.append((char, face))
        return runs

    def report(self):
        return {"key": self.policy.key, "warnings": list(self.policy.warnings), "used_faces": [
            {"font_id": f.font_id, "family": f.source.family, "path": str(f.source.path),
             "fs_type": f.source.fs_type, "bold": f.is_bold, "italic": f.is_italic,
             "synthetic_bold": f.synthetic_bold, "synthetic_italic": f.synthetic_italic}
            for f in self._faces.values() if f.font_id in self._used]}

    def draw_line(self, page, origin, text, *, fontsize=11, bold=False, italic=False, color=(0, 0, 0)):
        """Draw a horizontal mixed-script line; return real advance width.

        Layout/wrapping/clipping remain the caller's responsibility. Allow each
        face's ink_padding(fontsize), especially at the final run boundary.
        """
        x, y = origin
        start = x
        for substring, face in self.runs(text, bold=bold, italic=italic):
            face.insert_text(page, (x, y), substring, fontsize=fontsize, color=color)
            x += face.text_length(substring, fontsize=fontsize)
        return x - start


class BabelDOCFontPolicy:
    """Reversible mapper + synthetic rendering adapter; see module lifetime rules."""
    def __init__(self, chinese="", latin="", *, fonts_dir=None):
        self.chinese, self.latin = _names(chinese, latin)
        self.fonts_dir = fonts_dir
        self.resolver = None
        self._saved = []
        self._entered = False

    def report(self):
        return self.resolver.report() if self.resolver else {"key": "", "warnings": [], "used_faces": []}

    def __enter__(self):
        if self._entered or not _PATCH_LOCK.acquire(blocking=False):
            raise FontPolicyError("字体策略上下文不可重叠；请在独立文档工作进程中运行。")
        self._entered = True
        try:
            if not self.chinese:
                return self  # No PDF dependency import, validation or monkey patch.
            self.resolver = resolver = FitzFontResolver(self.chinese, self.latin, fonts_dir=self.fonts_dir)
            from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper, PrimaryFontFamily
            from babeldoc.format.pdf.document_il.backend.pdf_creater import CharacterRenderUnit
            old_init, old_map, old_render = FontMapper.__init__, FontMapper.map, CharacterRenderUnit.render
            faces = [resolver.face(family, bold, italic) for family in (self.chinese, self.latin)
                     for bold in (False, True) for italic in (False, True)]
            by_id = {face.font_id: face for face in faces}

            def initialize(mapper, translation_config):
                # Avoid BabelDOC asset lookup/download entirely for an explicit policy.
                mapper.translation_config = translation_config
                mapper.primary_font_family = PrimaryFontFamily.NONE
                mapper.fonts = by_id.copy()
                mapper.font_file_names = list(by_id)
                mapper.fontid2font = by_id.copy()
                mapper.fontid2fontpath = {key: face.font_path for key, face in by_id.items()}
                mapper.base_font = resolver.face(self.chinese)
                mapper.fontid2font["base"] = mapper.base_font
                mapper.fontid2fontpath["base"] = mapper.base_font.font_path
                mapper.normal_fonts = list(faces)
                mapper.script_fonts = []
                mapper.fallback_fonts = []
                mapper.normal_font_ids = list(by_id)
                mapper.script_font_ids = []
                mapper.fallback_font_ids = []
                mapper.base_font_ids = [mapper.base_font.font_id]
                mapper.type2font = {"normal": mapper.normal_fonts, "script": [], "fallback": [], "base": [mapper.base_font]}
                mapper.has_char = lru_cache(maxsize=10240)(lambda char: len(char) == 1 and bool(
                    resolver.face(self.chinese if _is_cjk(char) else self.latin).has_glyph(ord(char))))
                mapper._lf_font_resolver = resolver

            def map_font(mapper, original_font, char_unicode):
                if getattr(mapper, "_lf_font_resolver", None) is not resolver:
                    return old_map(mapper, original_font, char_unicode)
                bold = getattr(original_font, "bold", getattr(original_font, "is_bold", False))
                italic = getattr(original_font, "italic", getattr(original_font, "is_italic", False))
                return resolver.resolve(char_unicode, bold=bool(bold), italic=bool(italic))

            def render(unit, draw_op, context):
                face = by_id.get(unit.char.pdf_style.font_id) if unit.char.pdf_style else None
                if face is None:
                    return old_render(unit, draw_op, context)
                from bitstring import BitStream
                temporary = BitStream()
                old_render(unit, temporary, context)
                data = temporary.tobytes()
                if not data:
                    return
                size = unit.char.pdf_style.font_size
                # Modify only the generated text matrix, never source graphic state.
                matrix = (f"0 1 -1 {_SHEAR:f}" if unit.char.vertical else f"1 0 {_SHEAR:f} 1")
                if face.synthetic_italic:
                    data, count = re.subn(rb"(Tf )[-+\d.]+ [-+\d.]+ [-+\d.]+ [-+\d.]+ (?=[-+\d.]+ [-+\d.]+ Tm)",
                                         lambda m: m[1] + matrix.encode() + b" ", data, count=1)
                    if count != 1:
                        raise FontPolicyError("BabelDOC 字符绘制接口已变化，无法安全保留斜体。")
                if face.synthetic_bold:
                    before = data.split(b"BT /", 1)[0]
                    # Mirror fill operators at their original positions, including
                    # named ICC/Separation spaces. This keeps q/Q scope and uses
                    # exactly the same page/form resource, without converting ink.
                    operand = rb"(?:[-+]?(?:\d+(?:\.\d*)?|\.\d+)|/[^\s<>\[\](){}%/]+)"
                    colors = re.compile(rb"((?:" + operand + rb"\s+)+)(cs|scn|sc|rg|g|k)(?=\s|$)")
                    mirrored = colors.sub(lambda m: m[0] + b" " + m[1] + m[2].upper(), before)
                    expected = len(re.findall(rb"(?<![/\w])(cs|scn|sc|rg|g|k)(?=\s|$)", before))
                    if len(list(colors.finditer(before))) != expected:
                        raise FontPolicyError("填充色指令无法安全映射到合成粗体描边；已停止输出。")
                    data = b"q 0 G " + mirrored + data[len(before):] + b" Q "
                    prefix = f"{size * _STROKE_EM:f} w 1 J 1 j [] 0 d 2 Tr ".encode()
                    data = data.replace(b"BT /", prefix + b"BT /", 1)
                draw_op.append(_actual_text(data, unit.char.char_unicode))

            for cls, attr, new, old in ((FontMapper, "__init__", initialize, old_init),
                                       (FontMapper, "map", map_font, old_map),
                                       (CharacterRenderUnit, "render", render, old_render)):
                self._saved.append((cls, attr, old))
                setattr(cls, attr, new)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        for cls, attr, old in reversed(self._saved):
            setattr(cls, attr, old)
        self._saved.clear()
        if self._entered:
            self._entered = False
            _PATCH_LOCK.release()


# Function-style entry point for ExitStack/callers that prefer context managers.
def font_policy(chinese="", latin="", *, fonts_dir=None):
    return BabelDOCFontPolicy(chinese, latin, fonts_dir=fonts_dir)
