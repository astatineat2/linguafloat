from pathlib import Path
import unittest
import pymupdf as f
from linguafloat.document_clip import show_source_region

class ClipPaper(unittest.TestCase):
    def test_neighboring_ligature_at_clip_edge(self):
        files=list(Path('D:/翻译测试资料/生物').glob('14_*.pdf'))
        if not files:self.skipTest('private regression sample not installed')
        with f.open(files[0]) as src, f.open() as dest:
            p=src[1];r=f.Rect(70.0879974,88.9458008,260.8132935,96.7578125);t=dest.new_page(width=p.rect.width,height=p.rect.height)
            show_source_region(t,r,src,1)
            self.assertEqual(t.get_text(),p.get_text(clip=r));self.assertEqual(t.get_pixmap(clip=r,matrix=f.Matrix(2,2)).samples,p.get_pixmap(clip=r,matrix=f.Matrix(2,2)).samples)
