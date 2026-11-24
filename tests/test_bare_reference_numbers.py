import unittest
import pymupdf as f
from linguafloat.document_references import _digital_lines, numbered_evidence


class BareLabels(unittest.TestCase):
    def test_microscopic_space_rejoins_one_physical_line_not_a_column(self):
        from types import SimpleNamespace as NS
        def span(text, x, end, size):
            return dict(text=text,bbox=(x,10,end,18),origin=(x,16),size=size,flags=0)
        def line(*ss):
            return dict(spans=ss,bbox=(ss[0]['bbox'][0],10,ss[-1]['bbox'][2],18),dir=(1,0))
        rows=[line(span('Smith,',30,55,8)),line(span(' ',55,55.001,.001),span('J.',57,64,8)),
              line(span(' ',64,64.001,.001),span('Other column',220,280,8))]
        page=NS(get_text=lambda _:dict(blocks=[dict(lines=rows)]))
        self.assertEqual([r['text'].strip() for r in _digital_lines(page)], ['Smith, J.', 'Other column'])

    def test_continuation_opens_with_linked_report_then_dated_papers(self):
        from linguafloat.document_references import _reference_continues
        url = 'https://example.org/archive/report.pdf'
        with f.open() as doc:
            p = doc.new_page()
            for n, line in enumerate(['(5) Report of the commission.', 'https://example.org/archive/',
                                      'report.pdf.', '(6) Smith, J. Topic. Phys. Rev. (A) 2009, 4, 22.',
                                      '(7) Smith, J. Another topic. Phys. Rev. 2008, 3, 11.']):
                p.insert_text((40, 80+14*n), line)
            p.insert_link(dict(kind=f.LINK_URI, uri=url, **{'from':p.search_for('https://example.org/archive/')[0]}))
            p = doc.reload_page(p)
            rows = _digital_lines(p)
            self.assertTrue(_reference_continues(rows, 4, page=p))
            self.assertFalse(_reference_continues(rows, 3, page=p))
            rows[2] = dict(rows[2], text='other.pdf.')
            self.assertFalse(_reference_continues(rows, 4, page=p))

    def test_confirm_two_hanging_dated_citations_not_an_ordinary_list(self):
        for dated in (True, False):
            with self.subTest(dated=dated), f.open() as d:
                p=d.new_page()
                for i in (1,2):
                    p.insert_text((40,50+40*i), f'{i} J. Smith and T. Doe, A long paper title')
                    p.insert_text((52,64+40*i), 'Some Journal, 2020, 2(3), 125-127.' if dated else 'Consider the alternatives before use.')
                self.assertEqual(numbered_evidence([r['text'] for r in _digital_lines(p)]),dated)
