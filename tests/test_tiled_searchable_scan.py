import unittest
import pymupdf as f
from linguafloat.document_scan_layout import classify_page


class TiledScans(unittest.TestCase):
    def test_strips_cover_sheet_not_just_many_repeated_images(self):
        image=f.Pixmap(f.csRGB, f.IRect(0,0,4,4),False); image.clear_with(230)
        for mode,expected in [('tiled','scan_ocr'),('overlap','digital'),('visible','digital')]:
            with self.subTest(mode=mode), f.open() as doc:
                p=doc.new_page(width=300,height=400)
                for i in range(10):
                    y=0 if mode=='overlap' else i*40
                    p.insert_image(f.Rect(0,y,300,y+40),stream=image.tobytes('png'),keep_proportion=False)
                p.insert_text((22,55),'A searchable paragraph',render_mode=0 if mode=='visible' else 3)
                self.assertEqual(classify_page(p),expected)
