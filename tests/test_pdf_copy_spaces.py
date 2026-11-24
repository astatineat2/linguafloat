import unittest
from babeldoc.format.pdf.document_il.backend.pdf_creater import make_tounicode
from linguafloat.document_fonts import keep_mapped_spaces


class CopySpaces(unittest.TestCase):
    def test_outline_free_space_survives_font_map_reduction(self):
        mapping={7:32,9:65,11:160,12:0x0394}
        result=keep_mapped_spaces(make_tounicode)(mapping,[9]);self.assertIn('<0007><0020>',result)
        self.assertIn('<000b><00a0>',result);self.assertIn('<0009><0041>',result);self.assertNotIn('<000c>',result)
