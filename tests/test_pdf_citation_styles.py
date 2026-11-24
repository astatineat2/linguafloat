import pytest
from linguafloat.document_references import _author_year_citations


def rows(lines):
    return [dict(text=text, box=(x,y,x+190,y+8),size=8,horizontal=True) for x,y,text in lines]


@pytest.mark.parametrize('first,rest,name',[(20,20,'Smith, A.B., and Lee, R.'),(26,20,'Smith, A. B., and Lee, R.'),(20,30,'Smith AB & Lee R')])
def test_complete_multiauthor_citations_need_not_have_hanging_indent(first,rest,name):
    sample=rows([(first,20,name+' (2018). Measurements of heat.'),(rest,30,'J. Phys. 12: 311–320.'),(first,48,name+' (2020). Measured variation.'),(rest,58,'Phys. Lett. 19, 305–317.')]);assert len(_author_year_citations(sample))==2
    sample[1]['text']=sample[3]['text']='The results increased by 12 percent.'
    assert not _author_year_citations(sample)


def test_restore_unchanged_label_but_not_moved_or_changed_text():
    import pymupdf as pdf
    from linguafloat.document_publisher_footer import verified_native_links
    with pdf.open() as a,pdf.open() as b:
        s=a.new_page();t=b.new_page();uri='https://example.org/references/1'
        for pg in (s,t):pg.insert_text((30,60),'Journal article');pg.insert_text((30,100),'Other article' if pg is s else 'Other artwork')
        for label in ('Journal article','Other article'):s.insert_link(dict(kind=pdf.LINK_URI,uri=uri,**{'from':s.search_for(label)[0]}))
        s=a.reload_page(s);add=verified_native_links(s,t);assert len(add)==1 and tuple(add[0]['from'])==pytest.approx(tuple(s.search_for('Journal article')[0]),abs=.001)
        t.insert_link(add[0]);assert not verified_native_links(s,b.reload_page(t))


@pytest.mark.parametrize('printed,found',[('lab@example.org',1),('lab@example.org.uk',0),('otherlab@example.org',0)])
def test_mail_action_follows_unique_whole_address(printed,found):
    import pymupdf as pdf
    from linguafloat.document_publisher_footer import verified_native_links
    with pdf.open() as a,pdf.open() as b:
        s=a.new_page();t=b.new_page();s.insert_text((20,50),'Contact: lab@example.org');t.insert_text((80,110),printed)
        s.insert_link(dict(kind=pdf.LINK_URI,uri='mailto:lab@example.org',**{'from':s.search_for('Contact: lab@example.org')[0]}))
        assert len(verified_native_links(a.reload_page(s),t))==found
