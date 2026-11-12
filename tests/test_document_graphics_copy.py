"""Pinned engine lazy graphics must copy without walking persistent history."""
import copy

import pytest

from linguafloat.document_core import JobControl
from linguafloat.document_layout import LayoutPolicy


def long_style():
    from babeldoc.format.pdf.document_il import GraphicState, PdfStyle
    from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import (
        EMPTY_PASSTHROUGH_SNAPSHOT, LazyPassthroughInstruction, replace_first_passthrough_operator)
    snapshot = EMPTY_PASSTHROUGH_SNAPSHOT
    for index in range(1500):
        snapshot = replace_first_passthrough_operator(snapshot, 'w', ('w', str(index / 100)))
    lazy = LazyPassthroughInstruction(snapshot, suffix_parts=('0.2 0.3 0.4 rg',))
    return PdfStyle(font_id='original-font', font_size=9,
                    graphic_state=GraphicState(passthrough_per_char_instruction=lazy)), lazy


def test_deepcopy_long_graphics_history_preserves_instructions_and_isolation():
    from babeldoc.format.pdf.document_il import Box, PdfCharacter, PdfParagraph, PdfParagraphComposition
    style, lazy = long_style()
    glyph = PdfCharacter(pdf_style=style, box=Box(10, 20, 15, 29), char_unicode='A', pdf_character_id=65)
    paragraph = PdfParagraph(pdf_style=style, unicode='A',
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=glyph)])
    with LayoutPolicy(JobControl()):
        cloned = copy.deepcopy(paragraph)
    instruction = cloned.pdf_style.graphic_state.passthrough_per_char_instruction
    assert type(instruction) is str and instruction.encode() == lazy.encode()
    assert '14.99 w' in instruction and '0.2 0.3 0.4 rg' in instruction
    assert cloned.pdf_paragraph_composition[0].pdf_character.pdf_style is cloned.pdf_style
    assert cloned.pdf_paragraph_composition[0].pdf_character.pdf_character_id == 65
    assert cloned.pdf_paragraph_composition[0].pdf_character.box == glyph.box
    cloned.pdf_style.font_size = 12
    cloned.pdf_paragraph_composition[0].pdf_character.box.x = 99
    assert style.font_size == 9 and glyph.box.x == 10
    assert style.graphic_state.passthrough_per_char_instruction is lazy


@pytest.mark.parametrize('fail', [False, True])
def test_lazy_copy_hook_is_scoped_and_restored_on_exception(fail):
    from babeldoc.format.pdf.document_il.frontend.il_creater_active_support import LazyPassthroughInstruction
    before = LazyPassthroughInstruction.__dict__.get('__deepcopy__')
    try:
        with LayoutPolicy(JobControl()):
            style, lazy = long_style()
            assert copy.deepcopy(style).graphic_state.passthrough_per_char_instruction == str(lazy)
            if fail:
                raise RuntimeError('synthetic failure')
    except RuntimeError as exc:
        assert type(exc) is RuntimeError and fail
    assert LazyPassthroughInstruction.__dict__.get('__deepcopy__') is before


def test_failure_trace_keeps_repeated_frames_and_cause_without_private_values():
    import json
    from linguafloat.document_worker import failure_trace, failure_location
    secret = 'synthetic-secret-and-document-body'

    def recursive(depth):
        if depth:
            return recursive(depth - 1)
        try:
            raise ValueError(secret)
        except ValueError as cause:
            raise RecursionError(secret) from cause

    try:
        recursive(25)
    except RecursionError as exc:
        trace = failure_trace(exc)
        assert [item['type'] for item in trace['exceptions']] == ['RecursionError', 'ValueError']
        frames = trace['exceptions'][0]['frames']
        assert sum(frame['function'] == 'recursive' for frame in frames) == 26
        assert not trace['cause_chain_truncated']
        assert '递归重复位置' in failure_location(exc)
        assert secret not in json.dumps(trace)
        assert all('/' not in frame['file'] and '\\' not in frame['file'] for frame in frames)


def test_worker_persists_safe_full_failure_stack(tmp_path, monkeypatch):
    import io
    import json
    import logging
    import sqlite3
    from linguafloat import document_worker
    from linguafloat.document_core import DocumentJob
    from dataclasses import asdict

    source = tmp_path / 'sample.docx'
    source.touch()
    database = tmp_path / 'isolated.sqlite'
    job = DocumentJob(str(source), str(tmp_path / 'out'),
                      dict(id='test', base_url='https://invalid.test', api_mode='responses', model='mock'), job_id='test')
    incoming = io.TextIOWrapper(io.BytesIO((json.dumps(dict(job=asdict(job), database=str(database),
                        api_key='synthetic-private-key')) + '\n').encode()), encoding='utf8')
    outgoing_buffer = io.BytesIO()
    outgoing = io.TextIOWrapper(outgoing_buffer, encoding='utf8')

    def recursive(depth):
        if depth:
            return recursive(depth - 1)
        raise RecursionError('synthetic-private-key-and-body')

    monkeypatch.setattr(document_worker, 'execute', lambda *a, **kw: recursive(12))
    monkeypatch.setattr(document_worker.sys, 'stdin', incoming)
    monkeypatch.setattr(document_worker.sys, 'stdout', outgoing)
    logging_disabled = logging.root.manager.disable
    try:
        document_worker.main()
        outgoing.flush()
        protocol = json.loads(outgoing_buffer.getvalue())
        with sqlite3.connect(database) as db:
            status, raw = db.execute('SELECT status,details FROM jobs WHERE id=?', ('test',)).fetchone()
        details = json.loads(raw)
        frames = details['diagnostic']['exceptions'][0]['frames']
        assert status == 'failed' and protocol['type'] == 'error'
        assert sum(frame['function'] == 'recursive' for frame in frames) == 13
        assert 'synthetic-private-key' not in raw
        assert 'synthetic-private-key' not in protocol['message']
    finally:
        logging.disable(logging_disabled)
