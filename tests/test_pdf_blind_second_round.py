import unittest

from linguafloat.document_references import _bibliographic_publication


class SecondCorpus(unittest.TestCase):
    def test_only_proven_completely_off_page_text_may_skip_drawing(self):
        import pymupdf as f
        from linguafloat.document_pdf import _proven_offpage_text
        with f.open() as d:
            page=d.new_page(width=300,height=200)
            page.insert_text((50,214),'OFF THE PAGE',fontsize=9)
            run=page.get_texttrace()[0]; b=f.Rect(run['bbox']); region={'stage':'翻译','coordinates':'bottom-left'}
            self.assertTrue(_proven_offpage_text(page,b,region))
            self.assertFalse(_proven_offpage_text(page,b+(0,-12,0,0),region))
            self.assertFalse(_proven_offpage_text(page,b+(130,0,130,0),region))
            self.assertFalse(_proven_offpage_text(page,b,dict(region,kind='graphic_source')))

    def test_journal_volume_year_page_range(self):
        self.assertTrue(_bibliographic_publication('A study of libraries, Br. J. Educ. 113 Suppl (2015) S1–S5.'))
        self.assertTrue(_bibliographic_publication('An analysis, Nature 308 (2005) 1635–1638.'))
        self.assertFalse(_bibliographic_publication('The value 308 (2005) 1635 was recorded.'))

    def test_inferred_space_within_printed_anchor(self):
        from linguafloat.document_masthead import _printed_anchor
        self.assertEqual(_printed_anchor('Platform: Alpha Beta','AlphaBeta'),'Alpha Beta')
        self.assertIsNone(_printed_anchor('Platform: Alpha Beta or AlphaBeta','AlphaBeta'))
        self.assertIsNone(_printed_anchor('Platform: AlphaBetaPlus','AlphaBeta'))
