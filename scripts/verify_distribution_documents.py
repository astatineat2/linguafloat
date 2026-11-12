"""Exercise a distributed runtime on generated fixtures with a mock API only."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import socket
import sys
import time
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--bundle', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
args.output = args.output.resolve()
args.output.mkdir(parents=True, exist_ok=False)
os.environ.update(APPDATA=str(args.output / 'profile/roaming'),
                  LOCALAPPDATA=str(args.output / 'profile/local'))
sys.path.insert(0, str(args.bundle.resolve() / 'document-runtime/src'))
logging.disable(logging.CRITICAL)

import docx
import httpx
from lxml import etree
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont
import pptx
from pptx.util import Inches
import pymupdf as fitz
from linguafloat import __version__
from linguafloat.document_core import DocumentJob, DocumentTranslator, JobControl, JobStore
from linguafloat.document_worker import execute

calls = []


def synthetic(text):
    # Test output, not a claim of translation quality. Preserve numerical material.
    return re.sub(r'(<[^>]*>|\{[^}]*\})|[A-Za-z]+(?:[-\'][A-Za-z]+)*',
                  lambda m: m[0] if m[1] or len(m[0]) == 1 else '测译', text)


def respond(request):
    body = json.loads(request.content)
    messages = body.get('messages', body.get('input'))
    text = messages[-1]['content']
    calls.append(len(text))
    if text.lstrip().startswith('<text>'):
        node = etree.fromstring(text.encode())
        for child in node:
            child.text = synthetic(child.text or '')
        result = etree.tostring(node, encoding='unicode')
    else:
        result = synthetic(text.split('Now translate the following text:')[-1])
    return httpx.Response(200, json=dict(id='mock-distribution', object='chat.completion',
        created=1, model='offline-only', choices=[dict(index=0,
        message=dict(role='assistant', content=result), finish_reason='stop')],
        usage=dict(prompt_tokens=10, completion_tokens=10, total_tokens=20)))


def denied(*_args, **_kwargs):
    raise AssertionError('All external network denied in distribution smoke test')


fixtures = args.output / 'fixtures'
fixtures.mkdir()
pdf_file = fixtures / 'research.pdf'
with fitz.open() as document:
    page = document.new_page()
    page.insert_text((48, 65), 'The magnetic field decreases under the specified conditions.')
    page.insert_text((48, 95), 'The value is 3.5 T at 300 K. Equation (1) remains unchanged.')
    document.save(pdf_file)
word_file = fixtures / 'research.docx'
document = docx.Document()
paragraph = document.add_paragraph()
paragraph.add_run('The magnetic field ').bold = True
paragraph.add_run('decreases under the specified conditions.').italic = True
document.add_table(rows=1, cols=1).cell(0, 0).text = 'Temperature 300 K'
document.save(word_file)
ppt_file = fixtures / 'research.pptx'
presentation = pptx.Presentation()
slide = presentation.slides.add_slide(presentation.slide_layouts[6])
slide.shapes.add_textbox(Inches(1), Inches(1), Inches(7), Inches(2)).text = 'The magnetic field decreases.'
presentation.save(ppt_file)
image_file = fixtures / 'research.png'
image = Image.new('RGB', (1000, 240), 'white')
draw = ImageDraw.Draw(image)
draw.text((40, 70), 'The magnetic field decreases.', fill='black',
          font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 40))
image.save(image_file)
originals = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (pdf_file, word_file, ppt_file, image_file)}
results = []
started = time.monotonic()
with patch.object(socket.socket, 'connect', denied), patch.object(socket.socket, 'connect_ex', denied):
    for source in (pdf_file, word_file, ppt_file, image_file):
        output = args.output / source.suffix[1:]
        output.mkdir()
        job = DocumentJob(str(source), str(output), dict(id='offline-distribution', model='offline-only',
            api_mode='chat_completions', base_url='https://offline.invalid/v1', retries=0,
            document_concurrency=2, document_rpm=10000), mono=True, bilingual=True, pdf_mode='reading')
        store, control, events = JobStore(output / 'jobs.sqlite3'), JobControl(), []
        client = OpenAI(api_key='mock-only', base_url=job.provider['base_url'], max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(respond)))
        before = len(calls)
        try:
            translator = DocumentTranslator(job, '', store, control, events.append, client)
            execute(job, '', store, control, events.append, translator)
            final = events[-1]
            assert final.get('type') == 'finished', final.get('type')
            paths = [Path(p) for p in final['outputs']]
            assert len(paths) == 2 and all(p.is_file() for p in paths)
            if source.suffix == '.docx':
                mono = docx.Document(paths[0])
                assert '测译' in mono.paragraphs[0].text and mono.paragraphs[0].runs[0].bold
                assert len(docx.Document(paths[1]).paragraphs) >= 2
            elif source.suffix == '.pptx':
                assert '测译' in pptx.Presentation(paths[0]).slides[0].shapes[0].text
                assert len(pptx.Presentation(paths[1]).slides) == 2
            else:
                for path in paths:
                    with fitz.open(path) as doc:
                        assert len(doc) >= 1
                        assert any('测译' in page.get_text() for page in doc)
                if source.suffix == '.png':
                    # Real bundled local OCR must have produced source text for a request.
                    assert len(calls) > before
            assert hashlib.sha256(source.read_bytes()).hexdigest() == originals[source.name]
            results.append(dict(format=source.suffix, status='passed', outputs=[str(p) for p in paths],
                mock_requests=len(calls)-before, issues=final.get('issues', [])))
        except Exception as error:
            from linguafloat.document_worker import failure_trace
            results.append(dict(format=source.suffix, status='failed', error_type=type(error).__name__,
                                trace=failure_trace(error)))
        finally:
            client.close()
            store.close()
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
summary = dict(version=__version__, bundle=str(args.bundle), real_requests=0,
    synthetic_response_not_quality_evidence=True, external_network_blocked=True,
    seconds=round(time.monotonic()-started, 2), results=results)
(args.output / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
raise SystemExit(0 if all(r['status'] == 'passed' for r in results) else 1)
