"""Conservative bibliography boundaries for document translation."""
from __future__ import annotations

import re

from linguafloat.document_core import DocumentError


HEADING = re.compile(r"^(?:(?:\d+(?:\.\d+)*|[IVXLCDM]+)[.、)\s]+)?(?:references|bibliography|works cited|参考文献|参考资料|引用文献)\s*[:：]?$", re.I)
LABEL = re.compile(r"^\s*(?:\[(\d{1,3})\]|(\d{1,3})[.)、]|\((\d{1,3})\))\s*")
NUMBER = re.compile(LABEL.pattern + r"\S")
AUTHOR = re.compile(r"(?:[^\W\d_][\w'’\-]+(?:\s+[^\W\d_][\w'’\-]+)*,\s*(?:[A-Z]\.))"
                    r"|(?:(?:[A-Z]\.\s*){1,3}[^\W\d_][\w'’\-]+)")


DOI = re.compile(r"\b(?:doi\s*:\s*|(?:dx\.)?doi\.org/)?10\.\d{4,9}/[^\s<>]+", re.I)
COMPACT_CITATION = re.compile(
    r"^[^\W\d_][\w'\-]+\s+[A-Z]{1,3}(?:\s*(?:,|&|\band\b)\s*"
    r"[^\W\d_][\w'\-]+\s+[A-Z]{1,3})*\s+(?:18|19|20)\d{2}\s+"
    r"(?:[A-Z][A-Za-z]*\.?\s+){2,10}\d{1,4}\s+(?:[A-Za-z]?\d{4,}|\d+[-\u2013]\d+)\.?\s*$")


def _section_text(text):
    # A single publisher ornament, not arbitrary leading punctuation or words.
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"^[■▪●◆]\s*", "", text)


def is_heading(text):
    return bool(HEADING.fullmatch(_section_text(text)))


def numbered_evidence(lines):
    values = [int(next(g for g in m.groups() if g)) for text in lines if (m := NUMBER.match(text))]
    return any(a == 1 and b == 2 for a, b in zip(values, values[1:]))


def _digital_lines(page):
    rows = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            row = dict(text="".join(s["text"] for s in line["spans"]), box=line["bbox"],
                       size=max((s["size"] for s in spans), default=0),
                       horizontal=line.get("dir", (1, 0)) == (1, 0),
                       bold=bool(spans) and all(s.get("flags", 0) & 16 for s in spans))
            if (row['horizontal'] and len(spans)>1 and spans[0]['text'].strip() in {'■','▪','●','◆'}
                    and is_heading(row['text']) and spans[0]['size']>max(s['size'] for s in spans[1:])*1.5):
                # A decorative bullet's font box can start on the previous
                # line. Cut only in demonstrably blank space after that line.
                import pymupdf as f
                x,y,x2,y2=row['box']; title_top=min(s['bbox'][1] for s in spans[1:])
                ends=[p['box'][3] for p in rows if p['horizontal'] and p['box'][0]<x2
                      and p['box'][2]>x and p['box'][1]<y<p['box'][3]<title_top-3]
                if ends and not page.rotation:
                    cut=(max(ends)+title_top)/2
                    band=page.get_pixmap(matrix=f.Matrix(2,2),colorspace=f.csGRAY,alpha=False,
                                         clip=f.Rect(x,cut-1,x2,title_top-.5))
                    if band.samples and min(band.samples)>=248:
                        row['box']=(x,cut,x2,y2)
            # Some digital pages encode each word as a line beginning with a
            # microscopic space at the preceding word's endpoint. Check the
            # visible word as well: a spacer alone must never bridge columns.
            lead = line['spans'][0] if line['spans'] else None
            if (rows and spans and row['horizontal'] and rows[-1]['horizontal'] and lead['text'] == ' '
                    and lead['size'] <= .01 and abs(row['size'] - rows[-1]['size']) < .1):
                prev = rows[-1]; x,y,x2,y2 = prev['box']; a,b,c,d = row['box']
                if (abs(a-x2) <= .25 and abs(y-b) <= .25 and abs(y2-d) <= .25
                        and -.25 <= spans[0]['bbox'][0]-x2 <= row['size']*.75):
                    prev.update(text=prev['text']+row['text'], box=(x,min(y,b),max(x2,c),max(y2,d)),
                                bold=prev['bold'] and row['bold'])
                    continue
            # Some publishers put raised numbers on a shared baseline and do
            # not mark them as superscripts. Require a separate, smaller run.
            if len(spans) >= 2 and line.get("dir", (1, 0)) == (1, 0):
                label, rest = spans[0], spans[1:]
                body_size = max(s["size"] for s in rest)
                if (re.fullmatch(r"[1-9]\d{0,2}", label["text"].strip())
                        and .5 * body_size <= label["size"] <= .85 * body_size
                        and 0 <= rest[0]["bbox"][0] - label["bbox"][2] <= body_size):
                    row.update(citation_number=int(label["text"].strip()),
                               label_right=label["bbox"][2],
                               citation_text="".join(s["text"] for s in rest).strip())
            rows.append(row)
    # PDF text extraction can separate a hanging label ("1.\t") from its
    # author on the same baseline. Join only a nearby, same-sized text run;
    # never bridge a column gutter or attach a number to the following line.
    consumed = set()
    for row in rows:
        if not row["horizontal"] or not LABEL.fullmatch(row["text"]):
            continue
        x0, y0, x1, y1 = row["box"]
        candidates = [r for r in rows if r is not row and id(r) not in consumed
                      and r["horizontal"] and not LABEL.match(r["text"])
                      and 0 <= r["box"][0] - x1 <= row["size"] * 3
                      and abs(r["box"][1] - y0) <= 1.5
                      and abs(r["size"] - row["size"]) <= row["size"] * .2]
        if not candidates:
            continue
        body = min(candidates, key=lambda r: r["box"][0])
        row.update(text=row["text"].rstrip() + " " + body["text"],
                   box=(x0, min(y0, body["box"][1]), body["box"][2], max(y1, body["box"][3])),
                   bold=row["bold"] and body["bold"])
        consumed.add(id(body))
    rows = [r for r in rows if id(r) not in consumed]
    # Bare labels also occur in ordinary lists. Normalize diagnostic text only
    # for consecutive author-led entries with a dated, hanging publication.
    confirmed, candidates = set(), []
    for idx, row in enumerate(rows):
        match = re.match(r'^(\d{1,3})\s+(.*)', row['text'].strip())
        if not (match and row['horizontal'] and (AUTHOR.match(match[2]) or re.fullmatch(r'[A-Z]\.',match[2]))):
            continue
        candidates.append((idx, row, int(match[1]), match[2]))
        body = [match[2]]; x,y = row['box'][:2]; prev = y; size = row['size']
        for part in rows[idx+1:idx+15]:
            px,py = part['box'][:2]
            if not (part['horizontal'] and 2 < px-x < size*4 and 0 < py-prev < size*2.3):
                break
            body.append(part['text']); prev = py
        citation = ' '.join(body)
        if len(body)>1 and _bibliographic_entry(citation):
            confirmed.add(idx)
    groups=[]
    for item in candidates:
        _,r,n,_ = item
        if not groups or not (groups[-1][-1][2]+1==n and 0<r['box'][1]-groups[-1][-1][1]['box'][1]<r['size']*24
                and abs(r['box'][0]-groups[-1][-1][1]['box'][0])<r['size']):
            groups.append([])
        groups[-1].append(item)
    for group in groups:
        if sum(idx in confirmed for idx,_,_,_ in group)>=2:
            for _,r,num,text in group: r['text']=f'{num}. {text}'
    return rows


def _bibliographic_publication(text):
    """A dated publication suffix, independent of a possibly wrapped author."""
    year = re.search(r"\((?:18|19|20)\d{2}[a-z]?\)", text)
    publication = re.search(r"\b(?:arXiv|Journal|Proceedings)\b"
                             r"|\b[^\W\d_][\w. -]*,?\s+\d{1,4}"
                             r"(?:\s*\(\d{1,4}(?:[-\u2013]\d{1,4})?\))?,\s*[A-Za-z]*\d", text, re.I)
    # ACS and similar styles put a bare year before volume and pages/article ID.
    # A bare year or a URL alone must not turn an instruction into a citation.
    dated_publication = re.search(
        r"\b[^\W\d_][\w. &'’:\-]*(?:\s*\([A-Za-z. ]{1,24}\))?,?\s+(?:18|19|20)\d{2},\s*"
        r"\d{1,4}(?:\s*\(\d{1,3}\))?,\s*(?:No\.\s*)?[A-Za-z]*\d+\b", text)
    # IEEE places the bare year after explicitly labelled volume and pages.
    ieee_publication = re.search(
        r"\b[^\W\d_][\w. &'\u2019:\-]*,\s*vol\.\s*\d+"
        r"(?:\s*,\s*no\.\s*\d+)?\s*,\s*pp?\.\s*\d+"
        r"(?:\s*[-\u2013\u2212]\s*\d+)?\s*,\s*(?:18|19|20)\d{2}[a-z]?"
        r"(?=[.,;\s]*(?:$|doi\b|https?://))", text, re.I)
    # Elsevier-style volume (year) page range, sometimes with a supplement.
    # Require a journal-like suffix, not just a year or an isolated number.
    volume_year_pages = year and re.search(
        r",\s*[A-Za-z][\w. &'’\-]{1,80}\s+\d{1,4}(?:\s+Suppl\.?(?:\s+\d+)?)?"
        r"\s*\((?:18|19|20)\d{2}[a-z]?\)\s*[A-Za-z]?\d+(?:,\d{3})*\s*[-–−]\s*[A-Za-z]?\d+(?:,\d{3})*\b",text)
    # Some journals omit the article title or pages; books can name a publisher.
    dated_source = year and re.search(
        r"\b(?:Journal|Proceedings|Press|Nature|Science)(?:\s+\d{1,4}(?:\s*\(\d{1,3}\))?)?\s*"
        r"\((?:18|19|20)\d{2}[a-z]?\)\s*[.,;]?\s*$", text)
    dated_publisher = re.search(
        r"\b(?:University Press|Academic Press|Springer|Elsevier|Wiley),\s+(?:18|19|20)\d{2}\b", text)
    issue_without_pages = year and re.search(
        r"\b[A-Z][A-Za-z. &'\-]+,?\s+\d{1,4}\(\d{1,4}\)\.\s*$", text)
    return bool(DOI.search(text) or (year and publication) or dated_publication or issue_without_pages
                or ieee_publication or volume_year_pages or dated_source or dated_publisher)


def _bibliographic_entry(text):
    """Independent author, date and publication evidence, including Unicode names."""
    return bool(AUTHOR.match(text) and _bibliographic_publication(text) or COMPACT_CITATION.fullmatch(text))


def _aligned_author_entries(lines):
    # Flush-left and first-line-indent bibliographies do not satisfy the
    # hanging-indent detector. Require repeated complete journal citations.
    name = r"[^\W\d_][\w'’\-]+(?:\s+[^\W\d_][\w'’\-]+){0,2}?,?\s+[A-Z](?:\.|[A-Z]{0,3}(?=\s|,))"
    author = re.compile(r'^'+name+r'.{0,75}?(?:,\s*|\s+&\s+|\s+and\s+)'+name)
    journal = re.compile(r'\b[A-Z][A-Za-z. &]{1,65}\s+\d{1,4}\s*[:,]\s*(?:[A-Za-z]?\d+\s*[-–−]\s*\d+|[a-z]\d{3,}|\d{5,})\b')
    starts=[i for i,r in enumerate(lines) if r.get('horizontal') and r.get('size',0)>0 and author.match(r['text'].strip())]
    good=[]; consumed=set()
    for i in starts:
        if i in consumed:continue
        row=lines[i]; group=[row]; size=row['size']; x=row['box'][0]
        for r in lines[i+1:i+13]:
            if (not r.get('horizontal') or abs(r.get('size',0)-size)>size*.25 or abs(r['box'][0]-x)>size*2.5
                    or not -1<=r['box'][1]-group[-1]['box'][1]<=size*1.95):break
            if author.match(r['text'].strip()) and abs(r['box'][0]-x)<2 and any(re.search(r'\(\d{4}[a-z]?\)',g['text']) for g in group):break
            group.append(r)
        text=re.sub(r'(?<=[a-z])-\n(?=[a-z])','','\n'.join(r['text'].strip() for r in group)).replace('\n',' ')
        date=re.search(r'\((?:18|19|20)\d{2}[a-z]?\)',text)
        if len(group)>=2 and date and journal.search(text[date.end():]):
            good.append(row);consumed.update(range(i+1,i+len(group)))
    return [r for r in good if sum(abs(r['box'][0]-s['box'][0])<=2 for s in good)>=2]


def _author_year_citations(lines):
    """Confirm digital authors with hanging, independently complete citations."""
    lines = sorted(lines, key=lambda r: (r["box"][1], r["box"][0]))
    authors = [r for r in lines if r.get("horizontal") and r.get("size", 0) > 0
               and (AUTHOR.match(r["text"].strip()) or COMPACT_CITATION.fullmatch(r["text"].strip()))]
    if not authors:
        return _aligned_author_entries(lines)
    left = min(r["box"][0] for r in authors)
    anchors = [r for r in authors if abs(r["box"][0] - left) <= 3]
    confirmed = []
    for index, anchor in enumerate(anchors):
        if _bibliographic_entry(anchor['text'].strip()):
            confirmed.append(anchor)
            continue
        bottom = anchors[index + 1]["box"][1] if index + 1 < len(anchors) else float("inf")
        rows = []
        for r in lines:
            if not anchor["box"][1] <= r["box"][1] < bottom:
                continue
            if rows and r["box"][1] - rows[-1]["box"][3] > max(8, anchor["size"] * 1.5):
                break
            rows.append(r)
        if (len(rows) >= 2 and all(r.get("horizontal") and r["box"][0] >= left - 3 for r in rows)
                and any(r["box"][0] > left + max(3, anchor["size"] * .7) for r in rows[1:])
                and _bibliographic_entry(" ".join(r["text"].strip() for r in rows))):
            confirmed.append(anchor)
    if len(confirmed)>=2:return confirmed
    return _aligned_author_entries(lines) or confirmed


def _author_year_right_top(lines, split, left_anchors):
    right = [r for r in lines if r["box"][0] >= split]
    citations = _author_year_citations(right)
    if len(citations) < 2:
        return None
    first = citations[0]
    top = first["box"][1]
    prefix = [r for r in right if r["box"][1] < top and not (
        r['text'].strip().isdigit() and r['box'][0] > first['box'][0]+(first['box'][2]-first['box'][0])*.7
        and top-r['box'][3] > first['size']*2)]
    if not prefix:
        return top
    # Join a hanging title across the gutter only when the unfinished left
    # entry gains its missing publication from the right-column prefix.
    left = [r for r in lines if r["box"][0] < split and r["box"][1] >= left_anchors[-1]["box"][1]
            and not r["text"].strip().isdigit()]
    starts = [i for i, r in enumerate(left) if abs(r["box"][0] - left_anchors[-1]["box"][0]) <= 3
              and AUTHOR.match(r["text"].strip())]
    if starts:
        tail = " ".join(r["text"].strip() for r in left[starts[-1]:])
        suffix = " ".join(r["text"].strip() for r in prefix)
        size = first["size"]
        # A journal name alone can already pass _bibliographic_entry even
        # though its terminal volume and page range still cross the gutter.
        joined = tail + ' ' + suffix
        finishes_pages = (not tail.rstrip().endswith('.') and re.search(
            r',\s*\d{1,4},\s*\d{1,6}\s*(?:[-–−]|e)\s*\d{1,6}\.\s*$', joined))
        if ((not _bibliographic_entry(tail) or finishes_pages) and _bibliographic_entry(joined)
                and all(r["horizontal"] and r["box"][0] > first["box"][0] + max(3, size * .7)
                        and abs(r["box"][0] - prefix[0]["box"][0]) <= 3
                        and abs(r["size"] - size) <= size * .2 for r in prefix)
                and all(b["box"][1] - a["box"][3] <= max(12, size * 2)
                        for a, b in zip(prefix, prefix[1:] + [first]))
                and (suffix[:1].islower() or re.match(r"[\d(]", suffix))):
            return prefix[0]["box"][1]
    return top


def _numbered_citation_evidence(texts, start=None, count=2):
    """Use labels to group lines, then require independent citation metadata."""
    entries, current = [], []
    for text in texts:
        match = None if DOI.match(text.strip()) else NUMBER.match(text)
        if match:
            if current:
                entries.append(" ".join(current))
                if len(entries) == count:
                    break
            if start is not None and int(next(g for g in match.groups() if g)) != len(entries) + start:
                return False
            current = [LABEL.sub("", text, count=1)]
        elif current:
            current.append(text)
    else:
        if current:
            entries.append(" ".join(current))
    return len(entries) == count and all(_bibliographic_entry(text) for text in entries)


def _citation_lines(rows):
    return [f"[{r['citation_number']}] {r['citation_text']}" if r.get('citation_number') is not None
            else r['text'] for r in rows if r.get('horizontal', True)]


def _numbered_body_after_citations(rows):
    """An independent numbered block after real citations is not necessarily a citation."""
    starts = [i for i, row in enumerate(rows) if _reference_number(row) is not None]
    seen = False
    for start, stop in zip(starts, starts[1:] + [len(rows)]):
        row = rows[start]
        texts = _citation_lines(rows[start:stop])
        if _numbered_citation_evidence(texts, count=1):
            seen = True
        elif seen and row.get('horizontal', True) and not _bibliographic_publication(' '.join(texts)):
            entry = LABEL.sub('', row.get('citation_text', row['text']), count=1).strip()
            if entry and not AUTHOR.match(entry):
                return row
    return None


def _untitled_numbered_candidate(lines, page):
    """Require a continuous bibliography in column reading order, not y order.

    Unlike the raised-label fallback, this uses four complete citations per
    column and the layout detector, so lengthy appendices need not make a
    bibliography appear in the last forty percent of the file.
    """
    split = _reference_column_split(lines, page, {"page": -1}, page.number)
    columns = [[r for r in lines if split is None or r["box"][0] < split]]
    if split is not None:
        columns.append([r for r in lines if r["box"][0] >= split])
    for index, column in enumerate(columns):
        for first in column:
            match = NUMBER.match(first["text"])
            if not match or _reference_number(first) != 1:
                continue
            groups = []
            expected = 1
            for offset, current in enumerate(columns[index:]):
                top = first["box"][1] if offset == 0 else page.rect.height * .025
                anchors = sorted([r for r in current if r["box"][1] >= top - 1
                                  and NUMBER.match(r["text"])
                                  and not AFTER_REFERENCES.match(r["text"].strip())],
                                 key=lambda r: (r["box"][1], r["box"][0]))
                if (len(anchors) < 4 or [_reference_number(r) for r in anchors]
                        != list(range(expected, expected + len(anchors)))
                        or any(abs(r["box"][0] - anchors[0]["box"][0]) > 3 for r in anchors)):
                    break
                confirmed = []
                for pos, anchor in enumerate(anchors[:4]):
                    bottom = anchors[pos + 1]["box"][1] if pos + 1 < len(anchors) else page.rect.height * .94
                    continuation = sorted([r for r in current if r is not anchor
                                           and anchor["box"][1] - 1 <= r["box"][1] < bottom - 1
                                           and r["box"][0] >= anchor["box"][0] - 3],
                                          key=lambda r: (r["box"][1], r["box"][0]))
                    text = LABEL.sub("", anchor["text"], count=1) + " " + " ".join(r["text"] for r in continuation)
                    if not _bibliographic_entry(text):
                        break
                    confirmed.append(anchor)
                if len(confirmed) != 4:
                    break
                groups.append(confirmed)
                expected += len(anchors)
            if len(groups) == len(columns) - index:
                return dict(groups=groups, split=split, first_column=index)
    return None


def _untitled_numbered_boundary(page, number, candidate, prediction):
    import pymupdf as fitz
    regions = [(prediction.names[int(b.cls)], float(b.conf), fitz.Rect([float(v)/2 for v in b.xyxy]))
               for b in prediction.boxes]
    split = candidate["split"]
    boxes = []
    for index, entries in enumerate(candidate["groups"], candidate["first_column"]):
        first = fitz.Rect(entries[0]["box"])
        x0, x1 = (0, page.rect.width) if split is None else (0, split) if index == 0 else (split, page.rect.width)
        centers = [(fitz.Rect(r["box"]).tl + fitz.Rect(r["box"]).br) / 2 for r in entries]
        if any(name in {"figure", "table", "abandon", "isolate_formula"} and confidence >= .6
               and any(box.contains(center) for center in centers) for name, confidence, box in regions):
            return None
        plain = [box for name, confidence, box in regions if name == "plain text" and confidence >= .8
                 and box.x0 >= x0 - 3 and box.x1 <= x1 + 3
                 and (split is not None or box.width >= page.rect.width * .5)]
        if (not all(any((box + (-3, -3, 3, 3)).contains(center) for box in plain) for center in centers)
                or not any(abs(box.y0 - first.y0) <= 6 and (box + (-3, -3, 3, 3)).contains(centers[0]) for box in plain)
                or any(name in {"plain text", "title", "figure", "table"} and confidence >= .6
                       and box.x0 < x1 and box.x1 > x0 and box.y0 < first.y0 - 6 < box.y1
                       for name, confidence, box in regions)):
            return None
        boxes.append([x0, first.y0, x1, page.rect.height])
    return dict(page=number, y=boxes[0][1], title="参考文献（无标题）", scanned=False,
                column="full" if split is None else "both" if len(boxes) == 2 else "right",
                boxes=boxes, evidence="untitled-numbered-citations", confirmed_entries=4 * len(boxes))


def _untitled_candidate(lines, number, page_count, page):
    """A narrow fallback for digital, full-width, raised-number bibliographies.

    Four aligned entries, independently dated and bibliographic, are required.
    The caller must also confirm a separate text region with the layout model.
    OCR text and ordinary numbered lists cannot satisfy this font evidence.
    """
    if number < (page_count - 1) * .6:
        return None
    for first in lines:
        if first.get("citation_number") != 1:
            continue
        top = first["box"][1]
        if not page.rect.height * .06 < top < page.rect.height * .9:
            continue
        anchors = [r for r in lines if r["box"][1] >= top and "citation_number" in r
                   and abs(r["label_right"] - first["label_right"]) < 3]
        if [r["citation_number"] for r in anchors[:4]] != [1, 2, 3, 4]:
            continue
        entries = []
        for index, row in enumerate(anchors[:4]):
            bottom = anchors[index + 1]["box"][1] if index + 1 < len(anchors) else page.rect.height * .94
            continuation = [r for r in lines if row["box"][1] < r["box"][1] < bottom
                            and r["box"][0] >= first["box"][0] - 3
                            and r["box"][2] <= first["box"][2] + 15]
            text = row["citation_text"] + " " + " ".join(r["text"] for r in continuation)
            author = re.match(r"(?:[A-Z]\.\s*){1,3}[A-Z][\w'-]+", text)
            year = re.search(r"\((?:18|19|20)\d{2}[a-z]?\)", text)
            publication = re.search(r"\b(?:Proceedings|Journal|doi|arXiv)\b|\b(?:Phys|Rev|Lett)\.|[\u201c\"][^\u201d\"]{8,}[\u201d\"]", text)
            if not (author and year and publication):
                break
            entries.append(row)
        if len(entries) == 4:
            return entries
    return None


def _untitled_boundary(page, number, entries, prediction):
    import pymupdf as fitz
    first = fitz.Rect(entries[0]["box"])
    centers = [(fitz.Rect(r["box"]).tl + fitz.Rect(r["box"]).br) / 2 for r in entries]
    regions = [(prediction.names[int(b.cls)], float(b.conf), fitz.Rect([float(v)/2 for v in b.xyxy]))
               for b in prediction.boxes]
    if any(name in {"figure", "table", "abandon", "isolate_formula"} and confidence >= .6
           and any(box.contains(center) for center in centers) for name, confidence, box in regions):
        return None
    for name, confidence, box in regions:
        if (name != "plain text" or confidence < .8 or box.width < page.rect.width * .5
                or abs(box.y0 - first.y0) > 6
                or not all((box + (-3, -3, 3, 3)).contains(center) for center in centers)):
            continue
        # The new route only accepts one broad text column, not an ambiguous
        # cut through another column, figure, table, or preceding paragraph.
        if any(other_name in {"plain text", "title", "figure", "table"} and score >= .6
               and other.y1 > first.y0 and (other.x1 < box.x0 or other.x0 > box.x1
                                           or other.y0 < first.y0 - 6)
               for other_name, score, other in regions):
            continue
        return dict(page=number, y=float(first.y0), title="参考文献（无标题）", scanned=False,
                    column="full", boxes=[[0, first.y0, page.rect.width, page.rect.height]],
                    evidence="untitled-superscript-citations", confirmed_entries=4)
    return None


def _is_repeated_margin_line(source, number, row):
    """Recognize digital running furniture, not arbitrary right-aligned text.

    Require matching text and geometry within two pages, within the outer
    six percent of both pages. Only these lines can establish publisher bands.
    """
    page = source[number]
    x0, y0, x1, y1 = row["box"]
    height, width = page.rect.height, page.rect.width
    if not (y1 <= height * .06 or y0 >= height * .94):
        return False
    text = re.sub(r"\s+", " ", row["text"]).strip()
    if not text or is_heading(text) or NUMBER.match(text):
        return False
    # Facing-page running lines can alternate between the left and right edge.
    for adjacent in (number - 1, number + 1, number - 2, number + 2):
        if not 0 <= adjacent < len(source):
            continue
        other_page = source[adjacent]
        if other_page.rotation or other_page.cropbox.x0 or other_page.cropbox.y0:
            continue
        other_height, other_width = other_page.rect.height, other_page.rect.width
        if abs(other_height - height) > 3 or abs(other_width - width) > 3:
            continue
        for block in other_page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                other_text = re.sub(r"\s+", " ", "".join(span["text"] for span in line["spans"])).strip()
                box = line["bbox"]
                if (other_text == text
                        and (box[3] <= other_height * .06 or box[1] >= other_height * .94)
                        and all(abs(a - b) <= 3 for a, b in zip((x0, y0, x1, y1), box))):
                    return True
    return False


def _publisher_boxes(page, margin, other_lines):
    # Layout paragraphs may join the two ends of a running head, or extend
    # into the blank gutter. Include that whitespace, but never unmatched ink.
    boxes = []
    for row in margin:
        x0, y0, x1, y1 = row["box"]
        band = [0, max(0, y0 - 2), page.rect.width, min(page.rect.height, y1 + 2)]
        for other in other_lines:
            a, b, c, d = other["box"]
            if b >= band[3] or d <= band[1]:
                continue
            if c <= x0:
                band[0] = max(band[0], (c + x0) / 2)
            elif a >= x1:
                band[2] = min(band[2], (a + x1) / 2)
            else:
                break
        else:
            for existing in boxes:
                if (existing[0] == band[0] and existing[2] == band[2]
                        and existing[1] <= band[3] and band[1] <= existing[3]):
                    existing[1] = min(existing[1], band[1])
                    existing[3] = max(existing[3], band[3])
                    break
            else:
                boxes.append(band)
    return boxes


def boundary_intersects(boundary, number, box):
    if boundary and "page_boxes" in boundary:
        import pymupdf as fitz
        return any(fitz.Rect(box).intersects(fitz.Rect(area)) for area in reference_boxes(boundary, number))
    if not boundary or number < boundary["page"]:
        return False
    if number > boundary["page"]:
        return True
    import pymupdf as fitz
    return any(fitz.Rect(box).intersects(fitz.Rect(area)) for area in boundary["boxes"])


def filter_layout_paragraphs(page, boundary, retain=None):
    """Exclude source tail before any engine or bibliography requests."""
    if not boundary or page.page_number < boundary["page"]:
        return
    if page.page_number > boundary["page"] and "page_boxes" not in boundary:
        page.pdf_paragraph[:] = []
        return
    height = page.cropbox.box.y2
    boxes = boundary.get('pdf_boxes',{}).get(str(page.page_number)) or [(x0, height-y1, x1, height-y0) for x0,y0,x1,y1 in reference_boxes(boundary, page.page_number)]
    keep = []
    for paragraph in page.pdf_paragraph:
        if not (paragraph.unicode or "").strip():
            from linguafloat.document_scientific import composition_chars
            parts = paragraph.pdf_paragraph_composition
            if (not any((c.char_unicode or "").strip() for part in parts for c in composition_chars(part))
                    and not any(part.pdf_formula and (part.pdf_formula.pdf_curve or part.pdf_formula.pdf_form)
                                for part in parts)):
                # A 32pt decorative bullet can leave a whitespace-only gap
                # paragraph. It contains no text/ink to fail or restore.
                continue
        box = paragraph.box
        if box is None:
            raise DocumentError(f"第 {page.page_number + 1} 页段落缺少坐标，无法安全判断参考文献边界。")
        overlaps = [a for a in boxes if box.x < a[2] and box.x2 > a[0] and box.y < a[3] and box.y2 > a[1]]
        if overlaps:
            if is_heading(paragraph.unicode or ""):
                continue
            # Fonts include ascender/descender padding. A sliver touching the
            # next heading is not a crossing if every source glyph is outside.
            if all(min(box.y2,a[3])-max(box.y,a[1]) <= 1.5 for a in overlaps):
                from linguafloat.document_scientific import composition_chars
                parts=getattr(paragraph,'pdf_paragraph_composition',())
                glyphs=[c for p in parts for c in composition_chars(p) if (c.char_unicode or '').strip()]
                compact=lambda s: ''.join(s.split())
                if (glyphs and not any(p.pdf_formula for p in parts) and
                        compact(''.join(c.char_unicode for c in glyphs))==compact(paragraph.unicode or '') and
                        all(c.box and all(not (a[0] <= (c.box.x+c.box.x2)/2 <= a[2] and
                            a[1] <= (c.box.y+c.box.y2)/2 <= a[3]) for a in overlaps) for c in glyphs)):
                    keep.append(paragraph)
                    continue
            if not any(box.x >= a[0]-2 and box.x2 <= a[2]+2 and box.y >= a[1]-2 and box.y2 <= a[3]+2 for a in overlaps):
                error = DocumentError(f"第 {page.page_number + 1} 页参考文献边界穿过正文段落；该段保留原文，需核对。")
                if retain is None:
                    raise error
                retain(paragraph, error, "参考文献边界")
        else:
            keep.append(paragraph)
    page.pdf_paragraph[:] = keep


def retain_pdf_tail(target, source, boundary, *, offset=(0, 0)):
    """Clip the translated prefix and original tail without destructive redaction."""
    import pymupdf as fitz
    if source.rotation or source.cropbox.x0 or source.cropbox.y0:
        raise DocumentError("参考文献回填遇到未验证的旋转/裁剪坐标，已停止保存。")
    # Column tails can cut through a centred running page number. Splitting a
    # glyph between two projections duplicates its searchable text and changes
    # antialiasing at the seam. Move only intersecting source-glyph boundaries,
    # keeping the original character intact rather than repainting two halves.
    glyphs = []
    for block in source.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            chars = [c for span in line["spans"] for c in span["chars"] if c["c"].strip()]
            text = "".join(c["c"] for c in chars)
            # A running page number is one logical item, not separate clipped
            # digits on either side of the column gutter.
            box = fitz.Rect(line["bbox"])
            if text.isdigit() and (box.y1 <= source.rect.height * .06 or box.y0 >= source.rect.height * .94):
                glyphs.append(box)
            else:
                glyphs.extend(fitz.Rect(c["bbox"]) for c in chars)

    def subtract(rectangles, area):
        result = []
        for box in rectangles:
            overlap = box & area
            if overlap.is_empty:
                result.append(box)
            else:
                result.extend(r for r in (
                    fitz.Rect(box.x0, box.y0, box.x1, overlap.y0),
                    fitz.Rect(box.x0, overlap.y1, box.x1, box.y1),
                    fitz.Rect(box.x0, overlap.y0, overlap.x0, overlap.y1),
                    fitz.Rect(overlap.x1, overlap.y0, box.x1, overlap.y1)) if not r.is_empty)
        return result

    areas = [fitz.Rect(area) & source.rect for area in boundary["boxes"]]
    # Add cut glyphs individually, without growing a whole column or cascading
    # through neighboring line bboxes.
    areas += [glyph & source.rect for glyph in glyphs if not any(a.contains(glyph) for a in areas)
              and any((overlap := a & glyph).width > .01 and overlap.height > .01 for a in areas)]
    areas = [a for a in areas if not a.is_empty]
    if not areas:
        return
    shift = (*offset, *offset)
    placed = [area + shift for area in areas]
    destination = source.rect + shift if offset != (0, 0) else target.rect
    if not target.rect.contains(destination):
        raise DocumentError('参考文献回填区域超出译文面。')
    prefix = [source.rect]
    for area in areas:
        prefix = subtract(prefix, area)

    def rectangle(box):
        r = box * ~target.transformation_matrix
        return f"{r.x0:.8f} {r.y0:.8f} {r.width:.8f} {r.height:.8f} re\n".encode("ascii")

    def project(page, erased, path, dest):
        with fitz.open() as clean:
            clean.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
            from linguafloat.document_publisher_footer import _explicit_text_matrices
            if erased and not _explicit_text_matrices(clean[0]):
                raise DocumentError('参考文献文字恢复遇到未验证的内联图像流；已停止保存。')
            for box in erased:
                clean[0].add_redact_annot(box, fill=False, cross_out=False)
            if erased:
                # Remove searchable text behind the final clip. Leave original
                # vectors and images intact; the compound clip owns their paint.
                clean[0].apply_redactions(images=0, graphics=0, text=0)
            if clean[0].get_contents():
                target.show_pdf_page(dest, clean, 0)
                xref = target.get_contents()[-1]
                target.parent.update_stream(xref, b"q\n" + path + target.parent.xref_stream(xref) + b"\nQ")

    with fitz.open(stream=target.parent.tobytes(), filetype="pdf") as rendered:
        # Existing streams may be shared by every sheet of a bilingual PDF.
        # Detach this page instead of mutating the shared objects.
        xref = target.parent.get_new_xref()
        target.parent.update_object(xref, '<<>>')
        target.parent.update_stream(xref, b'')
        target.set_contents(xref)
        paths = [rectangle(area) for area in placed]
        outer = rectangle(target.rect)
        project(rendered[target.number], placed, b"".join(outer + path + b"W* n\n" for path in paths), target.rect)
        project(source, prefix, b"".join(paths) + b"W n\n", destination)
    # The same-coordinate projection replaces the anchors as well as the ink.
    # Keep adjacent actions and do not move internal destinations between PDFs.
    from linguafloat.document_masthead import external_web_uri
    for item in target.get_links():
        box = fitz.Rect(item['from'])
        if external_web_uri(target, item) and any(a.contains((box.tl+box.br)*.5) for a in placed):
            target.delete_link(item)
    for item in source.get_links():
        box = fitz.Rect(item['from'])
        if any(a.contains(box) for a in areas) and (uri := external_web_uri(source, item)):
            target.insert_link(dict(kind=fitz.LINK_URI, uri=uri, **{'from': box+shift}))


def require_boundary(boundary, kind):
    if boundary is None:
        raise DocumentError(f"已启用‘参考文献及后续内容不翻译’，但无法在{kind}中可靠确认正文参考文献标题和连续条目。未发送翻译请求；请检查章节结构或关闭此选项。")
    return boundary


def headed_numbered_columns(page, lines=None):
    """Identify a terminal bibliography whose second column starts above its title."""
    import pymupdf as fitz

    if page.rotation or page.cropbox.tl != fitz.Point(0, 0) or page.number != len(page.parent)-1:
        return None
    lines = _digital_lines(page) if lines is None else lines
    width, height = page.rect.width, page.rect.height
    mid = width / 2
    heads = [r for r in lines if is_heading(r['text']) and r['horizontal'] and r['bold']
             and r['box'][2] < mid and height*.08 < r['box'][1] < height*.9]
    if len(heads) != 1:
        return None
    head = heads[0]
    columns, numbers, tops = [], [], []
    for side in (0, 1):
        rows = sorted((r for r in lines if int(r['box'][0] >= mid) == side
                       and r['horizontal'] and r['box'][1] < height*.93
                       and (side or r['box'][1] > head['box'][3])), key=lambda r: r['box'][1])
        first = next((i for i, row in enumerate(rows) if NUMBER.match(row['text'])), None)
        if first is None or (not side and first):
            return None
        body = rows[first:]
        tops.append(body[0]['box'][1])
        entries = []
        for row in body:
            box = row['box']
            if (not side and box[2] > mid+1) or abs(row['size']-body[0]['size']) > 1.5:
                return None
            if match := NUMBER.match(row['text']):
                numbers.append(int(next(g for g in match.groups() if g)))
                entries.append(LABEL.sub('', row['text'], count=1))
            elif entries and box[0] > body[0]['box'][0]+2:
                entries[-1] += ' ' + row['text']
            else:
                return None
        if len(entries) < 3 or not all(_bibliographic_entry(entry) for entry in entries):
            return None
        top = (tops[-1] if side else head['box'][1]) - 2
        columns.append([mid if side else 0, top, width if side else mid, height])
    if tops[1] >= head['box'][1]-3 or numbers != list(range(1, len(numbers)+1)):
        return None
    # A full-width band or unrelated heading crossing the gutter is not a column tail.
    if any(r['box'][0] < mid-1 < r['box'][2] and r['box'][3] > min(b[1] for b in columns)
           and not r['text'].strip().isdigit() for r in lines):
        return None
    number = page.number
    return dict(page=number, y=head['box'][1], title=head['text'], scanned=False, column='both',
                boxes=columns, page_boxes={str(number): columns}, whole_pages=[], uncertain_pages=[],
                evidence='headed-terminal-numbered-columns', policy='bibliography-only-v3')


def _pdf_start(source, control, emit):
    """Return the first credible heading's page and top coordinate."""
    from linguafloat.document_pdf import DocumentOcr, has_scanned_text_layer, needs_page_ocr

    ocr = DocumentOcr()
    ocr.emit = emit
    layout_model = None
    def predict(page, number):
        nonlocal layout_model
        import numpy as np
        import pymupdf as fitz
        if layout_model is None:
            from babeldoc.docvision.doclayout import OnnxModel
            layout_model = OnnxModel.from_pretrained()
        control.checkpoint()
        emit(dict(type="progress", stage="解析中", value=None,
                  message=f"核对第 {number + 1} 页参考文献章节边界"))
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csRGB, alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        return layout_model.predict(rgb[:, :, ::-1].copy(), imgsz=1024)[0]

    for number, page in enumerate(source):
        control.checkpoint()
        if page.rotation or page.cropbox.x0 or page.cropbox.y0:
            from linguafloat.document_source_fidelity import cropped_bibliography
            verified=cropped_bibliography(page)
            if verified:return verified
            continue
        text = page.get_text("text")
        scanned = has_scanned_text_layer(page) or needs_page_ocr(page, text)
        if scanned:
            lines = ocr.recognize(page, positioned=True)
        else:
            lines = _digital_lines(page)
        lines.sort(key=lambda line: (line["box"][1], line["box"][0]))
        if any(re.fullmatch(r"(?:table of )?contents|目录|目\s+录", row["text"].strip(), re.I)
               and row["box"][1] < page.rect.height * .25 for row in lines):
            continue
        if sum(bool(re.search(r"(?:\.{3,}|…{2,}|·{3,})\s*\d+\s*$", row["text"])) for row in lines) >= 2:
            continue  # Contents pages can contain a References entry.
        if not scanned and (columns := headed_numbered_columns(page, lines)):
            return columns
        for line in lines:
            if not is_heading(line["text"]):
                continue
            if scanned and (line.get("confidence", 0) < .95 or not line.get("horizontal", False)):
                continue
            if not scanned:
                import pymupdf as fitz
                if any(fitz.Rect(image["bbox"]).contains(fitz.Rect(line["box"])) for image in page.get_image_info()):
                    continue  # Image labels are not document chapter headings.
            top = line["box"][1]
            if top < page.rect.height * .025 or top > page.rect.height * .94:
                continue  # Running headers and footers are not chapter headings.
            if not scanned and top < page.rect.height * .12:
                repeated_header = False
                for adjacent in (number - 1, number + 1):
                    if not 0 <= adjacent < len(source):
                        continue
                    repeated_header |= any(
                        is_heading("".join(span["text"] for span in other["spans"]))
                        and abs(other["bbox"][1] - top) < 3
                        for block in source[adjacent].get_text("dict")["blocks"] if "lines" in block
                        for other in block["lines"])
                if repeated_header:
                    continue
            middle = page.rect.width / 2
            following_rows = [r for r in lines if r["box"][1] > top + 2
                              and (line["box"][0] < middle < line["box"][2]
                                   or (r["box"][0] >= middle) == (line["box"][0] >= middle))]
            author_citations = _author_year_citations(following_rows) if not scanned else [
                r for r in following_rows if r.get('horizontal') and r.get('confidence', 0) >= .95
                and _bibliographic_entry(r['text'].strip())]
            evidence_rows = following_rows if scanned else _join_numbered_publication_lines(following_rows, None)
            following = _citation_lines(evidence_rows)
            numbered = _numbered_citation_evidence(following)
            # Only use the next page or column to complete an evidenced start.
            if not numbered and top > page.rect.height * .7 and _numbered_citation_evidence(following, count=1):
                if number + 1 < len(source):
                    numbered = _numbered_citation_evidence(following + source[number + 1].get_text('text').splitlines()[:70])
                if not numbered and line['box'][2] < middle:
                    other = [r for r in lines if r['box'][0] >= middle]
                    numbered = _numbered_citation_evidence(following + _citation_lines(other))
            joined_top=None
            if (not scanned and not numbered and len(author_citations)<2 and line['box'][2]<middle
                    and top>page.rect.height*.75):
                tail=sorted(following_rows,key=lambda r:r['box'][1]); right=sorted((r for r in lines if r['box'][0]>=middle),key=lambda r:r['box'][1]); cites=_author_year_citations(right)
                if tail and len(cites)>=2:
                    first=tail[0]; prefix=[r for r in right if r['box'][1]<cites[0]['box'][1] and not r['text'].strip().isdigit()]; joined=' '.join(r['text'].strip() for r in prefix)
                    # A flush-left publisher style may wrap the year to the
                    # second line, then carry the unfinished article title to
                    # the next column. Require the two halves, journal/pages,
                    # and several independent following citations together.
                    if (len(tail)==2 and len(prefix)==2 and len(cites)>=3
                            and AUTHOR.match(first['text'].strip())
                            and re.match(r'^\s*\((?:19|20)\d{2}[a-z]?\)\.\s+\S',tail[1]['text'])
                            and tail[1]['text'].rstrip()[-1:].islower() and prefix[0]['text'].lstrip()[:1].islower()
                            and re.fullmatch(r'[A-Z][\w &.,\-]+\s+\d+(?:\(\d+\))?,\s*\d+[-–]\d+\.',prefix[1]['text'].strip())
                            and _bibliographic_entry(' '.join(r['text'] for r in tail+prefix))
                            and abs(tail[1]['box'][0]-first['box'][0])<1
                            and all(abs(r['box'][0]-cites[0]['box'][0])<1 for r in prefix)
                            and all(r['horizontal'] and abs(r['size']-first['size'])<.3 for r in tail+prefix+[cites[0]])
                            and all(0<b['box'][1]-a['box'][1]<2.1*first['size'] for a,b in
                                    [(tail[0],tail[1]),(prefix[0],prefix[1]),(prefix[1],cites[0])])):
                        author_citations=[first];joined_top=prefix[0]['box'][1]
                    if (len(tail)<=4 and len(prefix) in (1,2,3) and AUTHOR.match(first['text'].strip())
                            and re.search(r'\((?:19|20)\d{2}\)',first['text']) and _bibliographic_entry(' '.join(r['text'] for r in tail)+' '+joined)
                            and re.fullmatch(r'[A-Z][\w &.,\-]+,\s*\d+(?:\(\d+\))?,\s*\d+[e–\-]\d+\.?',joined)
                            and all(r['horizontal'] and abs(r['size']-first['size'])<1 and r['box'][0]>cites[0]['box'][0]+3 for r in prefix)
                            and all(0<=b['box'][1]-a['box'][1]<2*first['size'] for a,b in zip(prefix,prefix[1:]+[cites[0]]))):
                        author_citations=[first];joined_top=prefix[0]['box'][1]
            if not numbered and len(author_citations) < 2 and joined_top is None:
                continue
            # A title and numbered labels can occur inside a scientific figure.
            # Validate candidates against the installed local layout detector.
            import pymupdf as fitz
            prediction = predict(page, number)
            center = (fitz.Rect(line["box"]).tl + fitz.Rect(line["box"]).br) / 2
            if any(prediction.names[int(item.cls)] in {"figure", "table", "abandon"}
                   and float(item.conf) >= .6 and fitz.Rect([float(v)/2 for v in item.xyxy]).contains(center)
                   for item in prediction.boxes):
                continue
            # A single horizontal cut cannot preserve reading order when a
            # column contains pre-heading body text below the cut.
            # A repeated author/journal header is not another body column.
            # Keep unconfirmed margin text and all scanned text conservative.
            column_lines = lines if scanned else [
                row for row in lines if not _is_repeated_margin_line(source, number, row)]
            left, right = line["box"][0], line["box"][2]
            anchors = [row for row in column_lines if row["box"][1] > top + 2
                       and (NUMBER.match(row["text"]) or row in author_citations)]
            right_anchors = [row for row in anchors if row["box"][0] >= page.rect.width * .5]
            left_body = [row for row in column_lines if row["box"][1] > top + 2 and
                         row["box"][2] < page.rect.width * .5 and len(row["text"].strip()) > 15]
            uncertain = False
            if not numbered and right < middle and any(r["box"][0] >= middle for r in column_lines):
                split = _reference_column_split(column_lines, page)
                if split is None:
                    continue
                right_top = joined_top if joined_top is not None else _author_year_right_top(column_lines, split, author_citations)
                column = "both" if right_top is not None else "left"
                boxes = [[0, top, split, page.rect.height]]
                if right_top is not None:
                    boxes.append([split, right_top, page.rect.width, page.rect.height])
            elif left >= page.rect.width * .5 and len(right_anchors) >= 2 and left_body:
                column = "right"
                left_edge = max(row["box"][2] for row in left_body)
                right_edge = min(row["box"][0] for row in right_anchors)
                if right_edge <= left_edge:
                    uncertain = True
                    boxes = [[0, 0, page.rect.width, page.rect.height]]
                else:
                    boxes = [[(left_edge+right_edge)/2, top, page.rect.width, page.rect.height]]
            elif any(row["box"][0] > page.rect.width * .54 and row["box"][1] < top - 2
                     and len(re.findall(r"[A-Za-z]{2,}|[\u4e00-\u9fff]", row["text"])) >= 3
                     and row["box"][2] - row["box"][0] > page.rect.width * .12 for row in column_lines):
                # A genuine second column requires geometric and citation order
                # evidence; otherwise keep this page visibly marked for review.
                left_refs = [r for r in anchors if r["box"][2] < page.rect.width * .5
                             and _reference_number(r) is not None]
                right_refs = [r for r in column_lines if r["box"][0] > page.rect.width * .5 and NUMBER.match(r["text"])]
                values = lambda rs: [_reference_number(r) for r in rs]
                right_rows = [r for r in column_lines if r["box"][0] >= middle]
                if (left_refs and right_refs and min(values(right_refs)) == max(values(left_refs)) + 1
                        and _numbered_citation_evidence(_citation_lines(right_rows), count=min(2, len(right_refs)))):
                    split = (max(r["box"][2] for r in left_refs) + min(r["box"][0] for r in right_refs)) / 2
                    boxes = [[0, top, split, page.rect.height], [split, min(r["box"][1] for r in right_refs), page.rect.width, page.rect.height]]
                    column = "both"
                else:
                    column, uncertain = "full", True
                    boxes = [[0, 0, page.rect.width, page.rect.height]]
            else:
                column = "full"
                boxes = [[0, top, page.rect.width, page.rect.height]]
            if not numbered and uncertain:
                # The new author-year route must not promote an ambiguous
                # column boundary into protection of unrelated page content.
                continue
            boundary = dict(page=number, y=float(top), title=line["text"].strip(), scanned=scanned,
                            column=column, boxes=boxes, uncertain_pages=[number] if uncertain else [])
            if not numbered:
                boundary["evidence"] = "headed-author-year-citations"
            return boundary
        if not scanned:
            entries = _untitled_candidate(lines, number, len(source), page)
            if entries:
                boundary = _untitled_boundary(page, number, entries, predict(page, number))
                if boundary:
                    return boundary
            candidate = _untitled_numbered_candidate(lines, page)
            if candidate:
                boundary = _untitled_numbered_boundary(page, number, candidate, predict(page, number))
                if boundary:
                    return boundary
    return None


def reference_boxes(boundary, number):
    """Page-local bibliography rectangles; legacy snapshots keep their old meaning."""
    if not boundary:
        return []
    if "page_boxes" in boundary:
        return (boundary.get("publisher_boxes", {}).get(str(number), [])
                + boundary["page_boxes"].get(str(number), []))
    return boundary["boxes"] if number == boundary["page"] else []


def reference_page_only(boundary, number):
    if not boundary:
        return False
    if "page_boxes" in boundary:
        return number in boundary.get("whole_pages", [])
    return number > boundary["page"]


def reference_after_end(boundary, number, box):
    end = boundary.get("end", {}) if boundary else {}
    if end.get("page") != number:
        return False
    split = end.get("split_x")
    if split is None or end.get("column") == "full":
        return box[1] >= end["y"]
    if end.get("column") == "right":
        return box[0] >= split and box[1] >= end["y"]
    return box[0] >= split or box[1] >= end["y"]


AFTER_REFERENCES = re.compile(
    r"^(?:(?:\d+(?:\.\d+)*|[IVXLCDM]+)[.\s]+)?"
    r"(?:appendi(?:x|ces)\b|supplement(?:ary|al)?\b|supporting information\b|"
    r"acknowledg(?:e)?ments?\b|author contributions\b|data availability\b|"
    r"methods\b|materials and methods\b|"
    r"conflicts? of interest\b|附录|补充材料|致谢|作者贡献|数据可用性)", re.I)


def _reference_column_split(lines, page, boundary=None, number=None):
    if boundary and number == boundary["page"]:
        if boundary.get("column") == "right":
            return boundary["boxes"][0][0] or None
        if boundary.get("column") in {"both", "left"}:
            return boundary["boxes"][0][2]
    # A centered page number spans the gutter but is not a body text column.
    # Ignore only bare digits in the outer margins, not numbered citations.
    lines = [r for r in lines if not (r.get("horizontal", False) and r["text"].strip().isdigit()
             and (r["box"][3] <= page.rect.height * .06 or r["box"][1] >= page.rect.height * .94))]
    middle = page.rect.width / 2
    left = [r for r in lines if r["box"][2] < middle and len(r["text"].strip()) > 15]
    right = [r for r in lines if r["box"][0] > middle and len(r["text"].strip()) > 15]
    if len(left) < 2 or len(right) < 2:
        return None
    if (max(min(r["box"][1] for r in left), min(r["box"][1] for r in right)) >= min(
            max(r["box"][3] for r in left), max(r["box"][3] for r in right))
            and not all(sum(_reference_number(r) is not None for r in column) >= 2
                        for column in (left, right))):
        return None
    split = (max(r["box"][2] for r in left) + min(r["box"][0] for r in right)) / 2
    top = min(r["box"][1] for r in left + right)
    if any(r["box"][0] < split < r["box"][2] and r["box"][1] >= top for r in lines):
        return None
    return split


def _reference_section_end(row, lines, page, split, scanned):
    text = _section_text(row["text"])
    if (not AFTER_REFERENCES.match(text) or len(text) >= 130
            or row.get("citation_number") is not None or re.match(r"\s*\[\d+\]", text)):
        return False
    if scanned and (row.get("confidence", 0) < .95 or not row.get("horizontal", False)):
        return False
    x0, y0, x1, y1 = row["box"]
    previous = [r for r in lines if r is not row and r["box"][1] < y0 - 1
                and r["box"][3] > page.rect.height * .06
                and (split is None or (r["box"][0] >= split) == (x0 >= split))]
    # A wrapped cited title may start with "Supplementary" or "Appendix".
    # Require an independent heading: page/column start, emphasis, or blank space.
    if not previous:
        following = sorted([r for r in lines if r["box"][1] >= y0
                            and (split is None or (r["box"][0] >= split) == (x0 >= split))],
                           key=lambda r: (r["box"][1], r["box"][0]))
        anchors = [r for r in following if _reference_number(r) is not None]
        if anchors and not row.get("bold"):
            anchor = anchors[0]
            leading = [r for r in following if r["box"][1] < anchor["box"][1]]
            size = row.get("size", (y1 - y0) * .7)
            if (x0 - anchor["box"][0] > max(3, size * .7)
                    and leading and all(r.get("horizontal") and abs(r["box"][0] - x0) <= 3
                                        and abs(r.get("size", size) - size) <= size * .2
                                        and (not scanned or r.get("confidence", 0) >= .95) for r in leading)
                    and all(b["box"][1] - a["box"][3] <= max(8, size)
                            for a, b in zip(leading, leading[1:]))
                    and _bibliographic_publication(" ".join(r["text"] for r in leading))):
                return False  # A dated hanging citation continues across the page/column break.
        return True
    closest = max(previous, key=lambda r: r["box"][3])
    anchors = [r for r in previous if _reference_number(r) is not None]
    if anchors:
        anchor = max(anchors, key=lambda r: r["box"][1])
        if (x0 - anchor["box"][0] > max(3, row.get("size", 0) * .7)
                and y0 - closest["box"][3] <= max(8, (y1 - y0) * .9)):
            return False  # An indented, even bold, annotation belongs to its citation.
    return bool(row.get("bold") and not closest.get("bold")
                or row.get("size", 0) > closest.get("size", float("inf")) * 1.1
                or y0 - closest["box"][3] > max(8, (y1 - y0) * .9))


def _reference_number(row):
    if row.get("citation_number") is not None:
        return row["citation_number"]
    if re.match(r'^\s*10\.\d{4,9}/\S',row['text']):
        return None  # A DOI wrapped onto its own line, not item number 10.
    match = NUMBER.match(row["text"])
    return int(next(g for g in match.groups() if g)) if match else None


def _join_numbered_publication_lines(lines, split):
    """A hanging publication line may begin with a number from the cited title."""
    result, entry = [], []
    column = None
    for row in lines:
        current_column = split is not None and row["box"][0] >= split
        if current_column != column:
            entry = []
            column = current_column
        if _reference_number(row) is not None:
            if (entry and row.get("citation_number") is None and row.get("horizontal")
                    and not AFTER_REFERENCES.match(_section_text(row["text"]))):
                previous = result[-1]
                text = LABEL.sub("", " ".join(r["text"] for r in entry), count=1).strip()
                # A volume at the end of one line can be followed by a flush
                # or outdented parenthesized issue, not a new citation label.
                wrapped_issue = bool(
                    re.search(r"\b(?:18|19|20)\d{2},\s*\d{1,4}\s*$", previous["text"])
                    and re.match(r"^\(\d{1,3}\),\s*(?:No\.\s*)?[A-Za-z]?\d", row["text"]))
                if ((row["box"][0] - entry[0]["box"][0] > max(3, row["size"] * .7)
                     or wrapped_issue)
                        and abs(row["box"][0] - previous["box"][0]) <= 3
                        and 0 <= row["box"][1] - previous["box"][1]
                        and row["box"][1] - previous["box"][3] <= max(8, row["size"])
                        and not AUTHOR.match(LABEL.sub("", row["text"], count=1).strip())
                        and not _bibliographic_entry(text)
                        and _bibliographic_entry(text + " " + row["text"])):
                    result[-1] = dict(previous, text=previous["text"] + " " + row["text"])
                    entry.append(row)
                    continue
            entry = [row]
        elif entry:
            entry.append(row)
        result.append(row)
    return result


def _reference_continues(lines, last_number, split=None, *, author_year=False, page=None):
    numbers = [n for row in lines if (n := _reference_number(row)) is not None]
    # Wrapped DOI suffixes such as "10.1016/..." are not numbered entries in
    # an independently confirmed author-year bibliography.
    if numbers and not author_year:
        texts = _citation_lines(lines)
        if _numbered_citation_evidence(texts, count=min(2, len(numbers))):
            return True
        if not (last_number is not None and numbers[0] == last_number + 1
                and all(b == a + 1 for a, b in zip(numbers, numbers[1:]))):
            return False
        # A linked institutional publication need not have a personal author or
        # journal date. Confirm its printed destination and the next two papers.
        if page is None or len(numbers) < 3:
            return False
        import pymupdf as fitz
        starts = [i for i, row in enumerate(lines) if _reference_number(row) is not None]
        first = lines[starts[0]:starts[1]]
        if not _numbered_citation_evidence(_citation_lines(lines[starts[1]:]), numbers[1], 2):
            return False
        printed = ''.join(re.sub(r'\s+', '', r['text']) for r in first).rstrip('.,;')
        return any(link.get('kind') == fitz.LINK_URI and (uri := link.get('uri', '')).startswith(('https://', 'http://'))
                   and printed.endswith(uri) and any(fitz.Rect(r['box']).intersects(link['from']) and
                   uri.startswith(r['text'].strip()) for r in first if r['text'].strip()) for link in page.get_links())
    columns = [lines] if split is None else [[r for r in lines if r["box"][0] < split],
                                            [r for r in lines if r["box"][0] >= split]]
    return all(len(_author_year_citations(column)) >= 2 for column in columns)


def pdf_boundary(source, control, emit):
    """Locate a bounded bibliography, rather than treating the document tail as one."""
    boundary = _pdf_start(source, control, emit)
    if boundary and boundary.get('evidence') in {'cropped-terminal-numbered-columns',
                                                  'headed-terminal-numbered-columns'}:
        return boundary
    if not boundary:
        return None
    from linguafloat.document_pdf import DocumentOcr, has_scanned_text_layer, needs_page_ocr
    areas, whole, splits, publisher = {}, [], {}, {}
    last_number = None
    ocr = None
    for number in range(boundary["page"], len(source)):
        control.checkpoint()
        page = source[number]
        if page.rotation or page.cropbox.x0 or page.cropbox.y0:
            raise DocumentError(f"第 {number + 1} 页参考文献范围使用未验证的旋转或裁剪坐标。")
        scanned = has_scanned_text_layer(page) or needs_page_ocr(page, page.get_text("text"))
        lines = _digital_lines(page)
        if scanned:
            if ocr is None:
                ocr = DocumentOcr()
                ocr.emit = emit
            lines = ocr.recognize(page, positioned=True)
        margin = [] if scanned else [r for r in lines if _is_repeated_margin_line(source, number, r)]
        column_lines = [r for r in lines if r not in margin]
        split = _reference_column_split(column_lines, page, boundary, number)
        if split is not None:
            splits[str(number)] = split
        lines.sort(key=lambda r: (split is not None and r["box"][0] >= split, r["box"][1], r["box"][0]))
        if not scanned:
            lines = _join_numbered_publication_lines(lines, split)
        boxes = boundary["boxes"] if number == boundary["page"] else [[0, 0, page.rect.width, page.rect.height]]
        # A cited title mentioning an appendix does not qualify as a heading.
        endings = [r for r in lines if _reference_section_end(r, lines, page, split, scanned)
                   and any(b[0] <= r["box"][0] < b[2] and b[1] <= r["box"][1] < b[3] for b in boxes)]
        end = endings[0] if endings else None
        prefix = lines[:lines.index(end)] if end else lines
        inside = [r for r in prefix if r['horizontal'] and not is_heading(r['text'])
                  and any(b[0] <= r['box'][0] < b[2] and b[1] <= r['box'][1] < b[3] for b in boxes)]
        numbered_body = _numbered_body_after_citations(inside)
        if numbered_body is not None:
            end = numbered_body
            prefix = lines[:lines.index(end)]
        if number > boundary["page"] and (not end or any(_reference_number(r) is not None for r in prefix)) and not _reference_continues(
                prefix, last_number, split, author_year=boundary.get("evidence") == "headed-author-year-citations",
                page=None if scanned else page):
            boundary["uncertain_end"] = dict(page=number, reason="unconfirmed-reference-continuation")
            emit({"type": "progress", "stage": "解析中", "value": None,
                  "message": f"第 {number + 1} 页未确认参考文献续页，不将后续正文自动纳入保护范围。"})
            break
        end_column = ("full" if split is None or (end and end["box"][0] < split < end["box"][2])
                      else "right" if end and end["box"][0] >= split else "left")
        if end and number not in boundary.get("uncertain_pages", []):
            clipped = []
            for x0, y0, x1, y1 in boxes:
                if end_column == "right":
                    if x0 < split:
                        clipped.append([x0, y0, min(x1, split), y1])
                    if x1 > split and end["box"][1] > y0:
                        clipped.append([max(x0, split), y0, x1, end["box"][1]])
                elif end_column == "left":
                    if x0 < split and end["box"][1] > y0:
                        clipped.append([x0, y0, min(x1, split), end["box"][1]])
                elif end["box"][1] > y0:
                    clipped.append([x0, y0, x1, end["box"][1]])
            boxes = clipped
        if boxes:
            areas[str(number)] = boxes
            if number > boundary["page"] and not end:
                whole.append(number)
            elif not scanned:
                furniture = _publisher_boxes(page, margin, column_lines)
                if furniture:
                    publisher[str(number)] = furniture
        if end:
            boundary["end"] = dict(page=number, y=end["box"][1], column=end_column,
                                   split_x=split, reason="numbered-body" if numbered_body is not None
                                   else "explicit-following-section")
            break
        numbers = [n for row in lines if any(b[0] <= row["box"][0] < b[2] and b[1] <= row["box"][1] < b[3]
                                             for b in boxes) and (n := _reference_number(row)) is not None]
        if numbers:
            last_number = numbers[-1]
    boundary.update(page_boxes=areas, whole_pages=whole, column_splits=splits, publisher_boxes=publisher,
                    policy="bibliography-only-v3")
    return boundary


def office_boundary(paragraphs, namespace, *, structured):
    """Find a heading in document order, excluding TOC and ordinary mentions."""
    for index, paragraph in enumerate(paragraphs):
        text = "".join(node.text or "" for node in paragraph.iter(f"{{{namespace}}}t")
                       if next(node.iterancestors(f"{{{namespace}}}p"), None) is paragraph).strip()
        if not is_heading(text):
            continue
        if structured:
            # Word headings have a heading style or outline level. TOC entries
            # have TOC styles and often a hyperlink/page number instead.
            style = paragraph.find(f"{{{namespace}}}pPr/{{{namespace}}}pStyle")
            outline = paragraph.find(f"{{{namespace}}}pPr/{{{namespace}}}outlineLvl")
            style_name = style.get(f"{{{namespace}}}val", "") if style is not None else ""
            if (any(link.get(f"{{{namespace}}}anchor", "").startswith("_Toc")
                    for link in paragraph.iter(f"{{{namespace}}}hyperlink"))
                    or any("Table of Contents" in str(node.attrib.values())
                           for ancestor in paragraph.iterancestors(f"{{{namespace}}}sdt")
                           for node in ancestor.iter(f"{{{namespace}}}docPartGallery"))):
                continue
            if not (re.match(r"^(?:Heading|标题)\d*", style_name, re.I) or outline is not None):
                continue
        following = paragraphs[index + 1:index + 30]
        following_text = ["".join(node.text or "" for node in candidate.iter(f"{{{namespace}}}t")
                                   if next(node.iterancestors(f"{{{namespace}}}p"), None) is candidate)
                          for candidate in following]
        numbered = numbered_evidence(following_text)
        automatic_numbering = False
        if structured:
            tagged = [p for p in following if p.find(f"{{{namespace}}}pPr/{{{namespace}}}numPr") is not None]
            automatic_numbering = len(tagged) >= 2
        dated_entries = sum(bool(re.search(r"\b(?:19|20)\d{2}\b", text)) for text in following_text)
        citation_entries = dated_entries >= 2 and sum(bool(text.strip()) for text in following_text) >= 2
        if numbered or automatic_numbering or (structured and citation_entries):
            return index
    return None
