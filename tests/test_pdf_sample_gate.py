import sys
import unittest
from pathlib import Path

import pymupdf as f

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from check_pdf_sample import compare
from linguafloat.document_publisher_footer import restore_printed_urls
from linguafloat.document_frontmatter import plan_frontmatter


class SampleGate(unittest.TestCase):
    def test_double_bond_left_behind_when_element_words_move(self):
        with f.open() as a, f.open() as b:
            s,t=a.new_page(),b.new_page()
            for p,dy in ((s,0),(t,25)):
                p.insert_text((40,80+dy),'C',fontsize=10); p.insert_text((53,80+dy),'O',fontsize=10)
                for y in (76,77.5):p.draw_line((47,y),(53,y))
            self.assertIn('detached-vector-bond',[v['rule'] for v in compare(s,t)])
            self.assertEqual(compare(s,s),[])

    def test_mail_with_no_orcid_and_unchanged_linked_icon(self):
        with f.open() as a,f.open() as b:
            s,t=a.new_page(),b.new_page();s.insert_text((45,60),'test@example.org')
            rect=s.search_for('test@example.org')[0]
            icon=f.Rect(25,25,36,36)
            for page in [s,t]:page.draw_rect(icon,fill=(0,.5,.3))
            for box,uri in [(rect,'mailto:test@example.org'),(icon,'https://example.org/state')]:s.insert_link({'kind':f.LINK_URI,'from':box,'uri':uri})
            s=a.reload_page(s); self.assertEqual(len(compare(s,t)),3)
            restore_printed_urls(s,t);t=b.reload_page(t)
            self.assertEqual(compare(s,t),[])
            restore_printed_urls(s,t);self.assertEqual(len(b.reload_page(t).get_links()),2)

    def test_affiliation_band_does_not_omit_intervening_rows(self):
        with f.open() as doc:
            p=doc.new_page(width=500,height=800)
            for i,txt in enumerate(['1Department of Medicine, University of Southern County, Division A','2School of Science','3Department of Medicine, University of Southern County, Division B,','West Campus, Australia']):p.insert_text((40,100+14*i),txt,fontsize=10)
            p.insert_text((40,400),'A'*66,fontsize=10)
            plan,=[q for q in plan_frontmatter(p) if q['kind']=='affiliations']
            self.assertEqual([l['text'][0] for l in plan['lines']],['1','2','3']);self.assertIn('Australia',plan['lines'][-1]['text'])

    def test_short_cells_and_numbered_rows(self):
        with f.open() as a,f.open() as b:
            s,t=a.new_page(),b.new_page()
            for page in [s,t]:
                page.insert_text((40,80),'Table 3');page.draw_line((40,90),(450,90));page.draw_line((40,175),(450,175));page.insert_text((45,125),'Leaves');page.insert_text((145,125),'DNA')
            for i in range(1,5):s.insert_text((40,200+14*i),f'{i}. Entry')
            t.insert_text((40,214),'1. A; 2. B; 3. C; 4. D')
            issues=compare(s,t);self.assertEqual([x['evidence'] for x in issues if x['rule']=='unchanged-short-cell'],['Leaves']);self.assertEqual(sum(x['rule']=='numbered-row-not-separate' for x in issues),3)

    def test_do_not_rebind_a_changed_icon_or_overwrite_a_new_field(self):
        with f.open() as a,f.open() as b:
            s,t=a.new_page(),b.new_page();s.insert_text((40,80),'other@example.org');t.insert_text((40,80),'Occupied by a different field')
            icon=f.Rect(30,30,48,48);s.draw_rect(icon,color=(1,0,0));t.draw_rect(icon,color=(0,0,1))
            for rect,uri in [(s.search_for('other@example.org')[0],'mailto:other@example.org'),(icon,'https://example.org/icon')]:s.insert_link({'kind':f.LINK_URI,'from':rect,'uri':uri})
            s=a.reload_page(s);restore_printed_urls(s,t);t=b.reload_page(t)
            self.assertEqual(t.get_links(),[]);self.assertIn('different field',t.get_text());self.assertNotIn('@',t.get_text())
