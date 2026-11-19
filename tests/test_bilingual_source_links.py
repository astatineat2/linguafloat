import pymupdf as fitz
import pytest
from urllib.parse import parse_qs, urlsplit

from linguafloat.document_core import DocumentJob, JobControl
from linguafloat.document_pdf import assemble_layout_pdf


def fixture_documents(tmp_path):
    source = tmp_path / "source.pdf"
    rendered = tmp_path / "rendered.pdf"
    with fitz.open() as pdf:
        for index in range(3):
            page = pdf.new_page(width=300, height=400)
            page.insert_text((30, 50), f"SOURCE {index}")
        pdf[1].insert_link(dict(kind=fitz.LINK_GOTO,
                                **{"from": fitz.Rect(28, 37, 90, 53)},
                                page=2, to=fitz.Point(30, 70), zoom=1.5))
        pdf[1].insert_link(dict(kind=fitz.LINK_GOTO,
                                **{"from": fitz.Rect(28, 60, 90, 73)},
                                page=0, to=fitz.Point(30, 50)))
        pdf[1].insert_link(dict(kind=fitz.LINK_URI,
                                **{"from": fitz.Rect(28, 80, 90, 93)},
                                uri="https://example.org/?a=1&b=2"))
        pdf.save(source)
    with fitz.open() as pdf:
        for index in range(4):
            pdf.new_page(width=300, height=400).insert_text((30, 50), f"TRANSLATED {index}")
        pdf.save(rendered)
    return source, rendered


@pytest.mark.parametrize("arrangement,source_pages,destination,offset", [
    ("side_by_side", [0, 1], 2, fitz.Point(0, 26)),
    ("alternating", [0], 3, fitz.Point(0, 0)),
])
def test_original_panel_keeps_cross_page_links_after_selected_page_assembly(
        tmp_path, arrangement, source_pages, destination, offset):
    source, rendered = fixture_documents(tmp_path)
    job = DocumentJob(str(source), str(tmp_path), {}, bilingual_layout=arrangement)
    paths = assemble_layout_pdf(job, [1, 2], rendered, tmp_path, {1: [3]}, JobControl())
    with fitz.open(next(p for p in paths if ".双语." in p)) as pdf:
        for number in source_pages:
            page = pdf[number]
            links = page.get_links()
            internal = [link for link in links if link["kind"] == fitz.LINK_GOTO]
            assert len(internal) == 1, "selected original-panel destination was lost or omitted page was misdirected"
            link = internal[0]
            assert link["page"] == destination
            assert abs(link["to"] - (fitz.Point(30, 70) + offset)) < .01
            current = page.first_link
            while current is not None and current.xref != link['xref']:
                current = current.next
            assert current is not None
            zoom = parse_qs(urlsplit(current.uri).fragment)['zoom'][0].split(',')[0]
            assert float(zoom) == pytest.approx(150)
            assert link["from"] == fitz.Rect(28, 37, 90, 53) + (offset.x, offset.y, offset.x, offset.y)
            assert [item["uri"] for item in links if item["kind"] == fitz.LINK_URI] == [
                "https://example.org/?a=1&b=2"]


def test_bilingual_link_restoration_does_not_change_drawings_or_copy_text(tmp_path):
    source, rendered = fixture_documents(tmp_path)
    job = DocumentJob(str(source), str(tmp_path), {})
    paths = assemble_layout_pdf(job, [0, 1, 2], rendered, tmp_path, {}, JobControl())
    with fitz.open(source) as original, fitz.open(next(p for p in paths if ".双语." in p)) as result:
        for index in range(3):
            page = result[index]
            clip = fitz.Rect(0, 26, 300, 426)
            assert page.get_textbox(clip).strip() == original[index].get_text().strip()
            assert original[index].get_pixmap().samples == page.get_pixmap(clip=clip).samples


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('arrangement', ['side_by_side', 'alternating'])
def test_original_links_keep_rotated_hotspot_and_target_coordinates(tmp_path, rotation, arrangement):
    source, rendered = fixture_documents(tmp_path)
    changed = tmp_path / 'rotated.pdf'
    with fitz.open(source) as pdf:
        pdf[1].set_rotation(rotation)
        pdf[2].set_rotation((rotation + 90) % 360)
        pdf.save(changed)
    job = DocumentJob(str(changed), str(tmp_path), {}, bilingual_layout=arrangement)
    paths = assemble_layout_pdf(job, [1, 2], rendered, tmp_path, {}, JobControl())
    with fitz.open(changed) as original, fitz.open(next(p for p in paths if '.双语.' in p)) as result:
        before = next(link for link in original[1].get_links() if link.get('page') == 2)
        after = next(link for link in result[0].get_links() if link['kind'] == fitz.LINK_GOTO)
        top = 26 if arrangement == 'side_by_side' else 0
        assert after['from'] == before['from'] + (0, top, 0, top)
        assert abs(after['to'] - (before['to'] + fitz.Point(0, top))) < .01


def test_original_self_link_is_not_duplicated(tmp_path):
    source, rendered = fixture_documents(tmp_path)
    changed = tmp_path / 'self.pdf'
    with fitz.open(source) as pdf:
        pdf[1].insert_link(dict(kind=fitz.LINK_GOTO, page=1, to=fitz.Point(30, 100),
                                **{'from': fitz.Rect(28, 100, 90, 113)}))
        pdf.save(changed)
    job = DocumentJob(str(changed), str(tmp_path), {}, bilingual_layout='alternating')
    paths = assemble_layout_pdf(job, [1, 2], rendered, tmp_path, {}, JobControl())
    with fitz.open(next(p for p in paths if '.双语.' in p)) as result:
        targets = sorted(link['page'] for link in result[0].get_links() if link['kind'] == fitz.LINK_GOTO)
        assert targets == [0, 2]


@pytest.mark.parametrize('destination', ['/XYZ 30 330 1.5', '/FitR 20 310 200 360'])
def test_named_destinations_resolve_from_original_pdf_name_tree(tmp_path, destination):
    source, rendered = fixture_documents(tmp_path)
    changed = tmp_path / 'named.pdf'
    # Non-ASCII PDF names can be decoded differently in get_links(). The
    # annotation's actual PDF string and original name tree are authoritative.
    name = fitz.get_pdf_str('section-\u03b1')
    with fitz.open(source) as pdf:
        pdf.xref_set_key(pdf.pdf_catalog(), 'Names',
                         f'<< /Dests << /Names [{name} [{pdf[2].xref} 0 R {destination}]] >> >>')
        page = pdf[1]
        page.insert_link(dict(kind=fitz.LINK_GOTO, page=2, to=fitz.Point(30, 70),
                              **{'from': fitz.Rect(28, 120, 90, 133)}))
        pdf.xref_set_key(page.annot_xrefs()[-1][0], 'A/D', name)
        pdf.save(changed)
    job = DocumentJob(str(changed), str(tmp_path), {})
    paths = assemble_layout_pdf(job, [1, 2], rendered, tmp_path, {}, JobControl())
    with fitz.open(next(p for p in paths if '.双语.' in p)) as result:
        links = [link for link in result[0].get_links()
                 if abs(link['from'].y0 - 146) < .01]
        assert len(links) == 1, 'original named destination was discarded during assembly'
        link = links[0]
        if destination.startswith('/XYZ'):
            assert link['page'] == 1
            assert abs(link['to'] - fitz.Point(30, 96)) < .01
        else:
            _, array = result.xref_get_key(link['xref'], 'A/D')
            assert array.startswith(f'[{result[1].xref} 0 R')
            assert '/FitR 20 310 200 360' in array


def test_mixed_scan_assembly_keeps_original_panel_cross_page_links(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from linguafloat.document_scan_layout import translate_mixed_layout_pdf

    source = tmp_path / 'blank-pages-with-links.pdf'
    with fitz.open() as pdf:
        pdf.new_page(width=300, height=400)
        pdf.new_page(width=300, height=400)
        pdf[0].insert_link(dict(kind=fitz.LINK_GOTO, page=1, to=fitz.Point(30, 70),
                                **{'from': fitz.Rect(28, 37, 90, 53)}))
        pdf.save(source)
    job = DocumentJob(str(source), str(tmp_path), {}, translate_tables=False)
    translator = SimpleNamespace(job=job, control=JobControl())

    class NoContentEnhancer:
        def __init__(self, *args):
            pass

        def apply(self, *args):
            pass

        def report(self, *args):
            pass

    monkeypatch.setattr('linguafloat.document_graphics.DocumentEnhancer', NoContentEnhancer)
    monkeypatch.setattr('linguafloat.document_postprocess.save_body_artifact', lambda *args: None)
    paths, _ = translate_mixed_layout_pdf(job, translator, tmp_path, lambda event: None,
                                         [0, 1], {0: 'empty', 1: 'empty'})
    with fitz.open(next(p for p in paths if '.双语.' in p)) as pdf:
        links = pdf[0].get_links()
        assert len(links) == 1 and links[0]['page'] == 1
        assert abs(links[0]['to'] - fitz.Point(30, 96)) < .01


def test_image_child_keeps_source_jumps_without_changing_parent(tmp_path, monkeypatch):
    from contextlib import closing
    from dataclasses import replace
    from types import SimpleNamespace
    from linguafloat.document_context import GraphicContext, SourceContext
    from linguafloat.document_core import JobStore
    from linguafloat.document_postprocess import save_body_artifact, translate_graphics_only, digest

    source, _ = fixture_documents(tmp_path)
    job = DocumentJob(str(source), str(tmp_path),
                      dict(id='local', model='mock', base_url='https://test.invalid/v1', api_mode='responses'),
                      job_id='parent', workflow='body', pdf_mode='layout', chinese_font='', latin_font='',
                      translate_tables=False)
    translator = SimpleNamespace(job=job, control=JobControl(), graphic_context=GraphicContext(),
                                  source_context=SourceContext(), set_source_context=lambda blocks: list(blocks))
    with fitz.open(source) as pdf:
        save_body_artifact(tmp_path, job, pdf,
                           [dict(source=i, start=i, count=1, selected=True) for i in range(3)], translator)
    folder = tmp_path / '.graphics-source'
    before = (folder / 'body.pdf').read_bytes()
    with closing(JobStore(tmp_path / 'jobs.sqlite')) as store:
        store.status(job, 'completed', dict(graphics_source=str(folder),
            reference_boundary={}, graphics_manifest_sha256=digest(folder / 'manifest.json')))
        child = replace(job, job_id='child', workflow='graphics', parent_job_id='parent',
                         graphics_source=str(folder), translate_figures=True)
        translator.job = child
        monkeypatch.setattr('linguafloat.document_graphics.apply_graphics', lambda *args: [])
        output = tmp_path / 'image-child'
        output.mkdir()
        paths, _ = translate_graphics_only(child, translator, output, lambda event: None, store=store)
    assert (folder / 'body.pdf').read_bytes() == before
    with fitz.open(next(p for p in paths if '.双语.' in p)) as pdf:
        links = [link for link in pdf[1].get_links() if link['kind'] == fitz.LINK_GOTO]
        assert sorted(link['page'] for link in links) == [0, 2]
