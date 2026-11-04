"""Recover skipped scan text with cropped OCR and separately protected formulas."""
from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher


def starts_numbered_item(text):
    return bool(re.match(r"^\s*(?:\[\d{1,3}\]|\d{1,3}[.)]|[a-z][)])\s*", text))


def has_prose(text):
    return bool(re.search(r"[A-Za-z]{3,}|[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]{2,}", text))


def native_prose_seeds(page, regions):
    """Hidden text locates missed rows; only subsequent visible OCR supplies text."""
    import pymupdf as fitz
    from linguafloat.document_scan_layout import normalize
    rows = {}
    for word in page.get_text("words"):
        rows.setdefault((word[5], word[6]), []).append(word)
    seeds = []
    for words in rows.values():
        text = " ".join(w[4] for w in words)
        if len(re.findall(r"[A-Za-z]{3,}", text)) < 2:
            continue
        box = fitz.Rect(words[0][:4])
        for word in words[1:]:
            box |= fitz.Rect(word[:4])
        if any(r.get("preserved") and fitz.Rect(r["box"]).contains((box.tl+box.br)/2) for r in regions):
            continue
        observations = [line for r in regions for line in r.get("lines", [])
                        if abs(line["box"][1]+line["box"][3]-box.y0-box.y1)/2 < box.height * .6
                        and (fitz.Rect(line["box"]) & box).width > box.width * .5]
        if any(SequenceMatcher(None, normalize(text), normalize(l["text"]), autojunk=False).ratio() >= .55 for l in observations):
            continue
        if any(abs(l["box"][1]+l["box"][3]-box.y0-box.y1)/2 < box.height * .3
               and (fitz.Rect(l["box"]) & box).width >= box.width * .85 for l in observations):
            continue
        line = dict(box=list(box), text=text, confidence=0, horizontal=True)
        seeds.append(dict(box=list((box+(-1, -1, 1, 1)) & page.rect), text=text, lines=[line],
                          label="native_seed", native_seed=True, reason="隐藏文字定位的遗漏行，等待可见 OCR 核验"))
    return seeds


def prose_word(text):
    token = text.strip(".,;:!?()[]\"'")
    if re.search(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", token):
        return True
    if token.lower() in {"sin", "cos", "tan", "exp", "log", "ln"}:
        return False
    if token.lower() in {"a", "i", "an", "as", "at", "be", "by", "do", "he", "if", "in", "is",
                         "it", "no", "of", "on", "or", "so", "to", "up", "us", "we"}:
        return token not in {"I", "A"}
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z'-]{2,}", token) and
                (token.islower() or token.isupper() or token.istitle()))


def discard_duplicate_observations(regions):
    """Drop only alternate observations covered by verified source-row owners."""
    import pymupdf as fitz
    result = []
    for index, region in enumerate(regions):
        box = fitz.Rect(region["box"])
        if box.is_empty or region.get("preserved"):
            result.append(region)
            continue
        owners = []
        for j, other in enumerate(regions):
            identities = other.get("verified_rows", []) + ([other["verified_row"]] if other.get("verified_row") else [])
            if index == j or not identities or (other.get("reason") and not other.get("preserved")):
                continue
            if region.get("verified_row") and not region.get("reason") and j > index:
                continue
            rect = fitz.Rect(other["box"])
            common = box & rect
            if common.is_empty or common.height < min(box.height, rect.height) * .7:
                continue
            # The source row identity is independent of potentially corrupt OCR text.
            if region.get("source_row", region.get("verified_row")) not in identities:
                continue
            owners.append(common)
        uncovered = [box]
        for rect in owners:
            next_parts = []
            for part in uncovered:
                hit = part & rect
                if hit.is_empty:
                    next_parts.append(part)
                    continue
                for coords in ((part.x0, part.y0, part.x1, hit.y0),
                               (part.x0, hit.y1, part.x1, part.y1),
                               (part.x0, hit.y0, hit.x0, hit.y1),
                               (hit.x1, hit.y0, part.x1, hit.y1)):
                    piece = fitz.Rect(coords)
                    if not piece.is_empty:
                        next_parts.append(piece)
            uncovered = next_parts
        if sum(r.get_area() for r in uncovered) > box.get_area() * .08:
            result.append(region)
    return result


def separate_verified_rows(regions):
    """Separate layout padding without changing already validated erasure masks."""
    import pymupdf as fitz
    for i, first in enumerate(regions):
        if first.get("reason") or not first.get("verified_row"):
            continue
        for second in regions[i + 1:]:
            if second.get("reason") or not second.get("verified_row") or first["verified_row"] == second["verified_row"]:
                continue
            upper, lower = sorted((first, second), key=lambda r: r["box"][1])
            a, b = fitz.Rect(upper["box"]), fitz.Rect(lower["box"])
            hit = a & b
            if hit.is_empty or hit.height > min(1.5, min(a.height, b.height) * .2) or hit.width < min(a.width, b.width) * .7:
                continue
            cut = (a.y1 + b.y0) / 2
            for region in (upper, lower):
                region.setdefault("erase_box", list(region["box"]))
                region.setdefault("erase_lines", region["lines"])
            upper["box"] = [a.x0, a.y0, a.x1, cut-.125]
            lower["box"] = [b.x0, cut+.125, b.x1, b.y1]


class Recovery:
    def __init__(self, page, analyzer, digest):
        import cv2
        import numpy as np
        import pymupdf as fitz
        self.page, self.analyzer, self.digest = page, analyzer, digest
        self.native_words = page.get_text("words")
        rows = {}
        for word in self.native_words:
            rows.setdefault((word[5], word[6]), []).append(word)
        self.native_rows = []
        for words in rows.values():
            box = fitz.Rect(words[0][:4])
            for word in words[1:]:
                box |= fitz.Rect(word[:4])
            self.native_rows.append((box, " ".join(w[4] for w in words)))
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), colorspace=fitz.csGRAY, alpha=False)
        data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        _, self.labels, self.stats, self.centers = cv2.connectedComponentsWithStats((data < 200).astype(np.uint8), 8)
        self.stats[0, cv2.CC_STAT_AREA] = 0

    def tight_box(self, box, fence):
        """Use actual glyph extents, bounded by neighboring word-run ownership."""
        import cv2
        import numpy as np
        import pymupdf as fitz
        box = (fitz.Rect(box) + (-1.5, -1, 1.5, 1)) & fitz.Rect(fence) & self.page.rect
        c, s = self.centers / 3, self.stats
        chosen = ((s[:, cv2.CC_STAT_AREA] > 3) & (c[:, 0] >= box.x0) & (c[:, 0] <= box.x1)
                  & (c[:, 1] >= box.y0) & (c[:, 1] <= box.y1)
                  & (s[:, cv2.CC_STAT_HEIGHT] / 3 <= box.height * 1.5))
        indices = np.flatnonzero(chosen)
        if not len(indices):
            return None
        parts = s[indices]
        rect = fitz.Rect(float(parts[:, 0].min()) / 3, float(parts[:, 1].min()) / 3,
                         float((parts[:, 0] + parts[:, 2]).max()) / 3,
                         float((parts[:, 1] + parts[:, 3]).max()) / 3)
        # Word-box edges are estimates. Keep complete components; the final
        # collision check rejects real conflicts instead of clipping glyphs.
        return list((rect + (-.5, -.5, .5, .5)) & self.page.rect)

    def recognize(self, original):
        import pymupdf as fitz
        store = getattr(self.analyzer.translator, "store", None)
        box = (fitz.Rect(original["box"]) + (-2, -2, 2, 2)) & self.page.rect
        signature = hashlib.sha256(json.dumps(list(box)).encode()).hexdigest()
        key = f"scan-local-words-{'seed-v1' if original.get('native_seed') else 'v1'}:{self.digest}:{self.page.number}:{signature}"
        cached = store.get(key) if store else None
        if cached:
            try:
                lines = json.loads(cached)
                if lines:
                    return lines
            except (ValueError, TypeError):
                pass
        self.analyzer.translator.control.checkpoint()
        lines = self.analyzer.ocr.recognize(self.page, positioned=True, clip=box, scale=4, words=True)
        if not lines and len(original.get("lines", [])) == 1:
            lines = self.analyzer.ocr.recognize(self.page, positioned=True, clip=box, scale=4, words=True, single_line=True)
        if store:
            store.put(key, json.dumps(lines, ensure_ascii=False))
        return lines

    def region(self, original):
        import pymupdf as fitz
        from linguafloat.document_scan_layout import ink_patch, normalize, risky_math
        if not has_prose(original["text"]):
            hidden = self.page.get_text("text", clip=fitz.Rect(original["box"]))
            if not has_prose(hidden):
                return [dict(original, preserved=True)]
        lines = self.recognize(original)
        if original.get("native_seed") and lines:
            best = max(lines, key=lambda l: SequenceMatcher(None, normalize(original["text"]), normalize(l["text"]), autojunk=False).ratio())
            lines = [best]
        candidates = []
        parent = fitz.Rect(original["box"])
        for line in lines:
            rect = fitz.Rect(line["box"])
            if not parent.contains((rect.tl + rect.br) / 2):
                continue
            words = line.get("words", [])
            if not words or not line["horizontal"]:
                candidates.append(dict(original, box=list(rect), text=line["text"], lines=[line]))
                continue
            # Confidence alone cannot override conflicting source evidence.
            raw = [l["text"] for l in original.get("lines", [])
                   if abs((l["box"][1] + l["box"][3] - rect.y0 - rect.y1) / 2) < rect.height * .65]
            native = [w for w in self.native_words if rect.contains(fitz.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))]
            hidden = " ".join(w[4] for w in native)
            hidden_agrees = SequenceMatcher(None, normalize(hidden), normalize(line["text"]), autojunk=False).ratio() >= .85
            agrees = max((SequenceMatcher(None, normalize(t), normalize(line["text"]), autojunk=False).ratio()
                          for t in [*raw, hidden]), default=0) >= .85
            if not agrees and line["confidence"] < .99:
                candidates.append(dict(original, box=list(rect), text=line["text"], lines=[line]))
                continue
            mixed = risky_math(line["text"])
            # Detector rows can include the neighboring baseline. Assign ink
            # by row centers before expanding to complete glyph components.
            row_top, row_bottom = rect.y0, rect.y1
            center = (rect.y0 + rect.y1) / 2
            matches = [(SequenceMatcher(None, normalize(text), normalize(line["text"]), autojunk=False).ratio(), box)
                       for box, text in self.native_rows if abs((box.y0 + box.y1)/2-center) < rect.height
                       and min(box.x1, rect.x1) > max(box.x0, rect.x0)]
            match = max(matches, key=lambda item: item[0], default=(0, None))
            if match[0] >= .8:
                native_row = match[1]
                center = (native_row.y0 + native_row.y1)/2
                row_top, row_bottom = native_row.y0, native_row.y1
                for other, text in self.native_rows:
                    other_center = (other.y0 + other.y1)/2
                    if min(native_row.x1, other.x1) <= max(native_row.x0, other.x0):
                        continue
                    if center + 3 < other_center < center + native_row.height * 1.8:
                        row_bottom = min(row_bottom, (center + other_center)/2)
                    elif center - native_row.height * 1.8 < other_center < center - 3:
                        row_top = max(row_top, (center + other_center)/2)
            for neighbor in lines:
                other = fitz.Rect(neighbor["box"])
                if neighbor is line or min(rect.x1, other.x1) <= max(rect.x0, other.x0):
                    continue
                other_center = (other.y0 + other.y1) / 2
                if other_center < center - 2:
                    row_top = max(row_top, (center + other_center) / 2)
                elif other_center > center + 2:
                    row_bottom = min(row_bottom, (center + other_center) / 2)
            if mixed:
                groups = []
                for word in words:
                    good = prose_word(word["text"])
                    if not groups or groups[-1][0] != good:
                        groups.append((good, []))
                    groups[-1][1].append(word)
            else:
                groups = [(has_prose(line["text"]), words)]
            for index, (good, group) in enumerate(groups):
                text = " ".join(w["text"] for w in group) if mixed else line["text"]
                bounds = fitz.Rect(group[0]["box"])
                for word in group[1:]:
                    bounds |= fitz.Rect(word["box"])
                fence = fitz.Rect(parent.x0 - 2, row_top, parent.x1 + 2, row_bottom) & self.page.rect
                if index:
                    fence.x0 = (groups[index - 1][1][-1]["box"][2] + group[0]["box"][0]) / 2
                if index + 1 < len(groups):
                    fence.x1 = (group[-1]["box"][2] + groups[index + 1][1][0]["box"][0]) / 2
                tight = self.tight_box(bounds, fence)
                if tight is None:
                    candidates.append(dict(original, box=list(bounds), text=text, lines=[dict(line, box=list(bounds), text=text)]))
                    continue
                reason = "" if good else "公式、符号或数字保持原位"
                if good and not hidden_agrees and min(w["confidence"] for w in group) < .9:
                    reason = "局部高分辨率 OCR 仍有低置信度文字"
                candidate = dict(box=tight, text=text, lines=[dict(line, box=tight, text=text)],
                                 reason=reason, label="recovered", recovered=True, preserved=not good,
                                 full_line=not mixed)
                if match[0] >= .85:
                    candidate["verified_row"] = tuple(round(v, 2) for v in match[1])
                if mixed and good:
                    candidate["context"] = original["text"]
                if not reason:
                    _, candidate["reason"] = ink_patch(self.page, candidate)
                candidates.append(candidate)
        if not candidates:
            return [original]
        # A crop must account for every original prose line before replacing it.
        for old in original.get("lines", []):
            if has_prose(old["text"]) and not any(
                    abs((old["box"][1] + old["box"][3] - r["box"][1] - r["box"][3]) / 2)
                    < (old["box"][3] - old["box"][1]) * .8 for r in candidates):
                candidates.append(dict(original, box=list(old["box"]), text=old["text"], lines=[old]))
        joined = []
        for candidate in candidates:
            can_join = lambda r: not r["reason"] or r["reason"].startswith("墨迹接触区域边界")
            if (joined and can_join(candidate) and candidate.get("full_line") and
                    not starts_numbered_item(candidate["text"]) and
                    can_join(joined[-1]) and joined[-1].get("full_line") and
                    0 <= candidate["box"][1] - joined[-1]["box"][3] < 10):
                previous = joined[-1]
                merged = dict(previous, box=list(fitz.Rect(previous["box"]) | fitz.Rect(candidate["box"])),
                              text=previous["text"] + " " + candidate["text"],
                              lines=previous["lines"] + candidate["lines"])
                _, reason = ink_patch(self.page, merged)
                if not reason:
                    merged["reason"] = ""
                    merged["verified_rows"] = [*previous.get("verified_rows", []),
                                               *([previous["verified_row"]] if previous.get("verified_row") else []),
                                               *([candidate["verified_row"]] if candidate.get("verified_row") else [])]
                    merged.pop("verified_row", None)
                    joined[-1] = merged
                    continue
            joined.append(candidate)
        return joined


def recover_regions(page, regions, analyzer, digest):
    import pymupdf as fitz
    from linguafloat.document_scan_layout import ink_patch
    pending = []
    for region in regions:
        if not region.get("reason"):
            _, reason = ink_patch(page, region)
            if reason:
                region = dict(region, reason=reason)
        pending.append(region)
    pending.extend(native_prose_seeds(page, pending))
    if not any(r.get("reason") and has_prose(r["text"]) for r in pending):
        return pending
    recovery = Recovery(page, analyzer, digest)
    result = []
    for i, region in enumerate(pending):
        analyzer.translator.control.checkpoint()
        if region.get("reason") and not region.get("preserved"):
            analyzer.emit({"type": "progress", "stage": "OCR 中", "value": None,
                           "message": f"局部补识别：第 {page.number + 1} 页，区域 {i + 1}/{len(pending)}"})
            result.extend(recovery.region(region))
        else:
            result.append(region)
    # Adjacent footnotes may arrive as separate OCR regions. Rejoin their rows
    # before rejecting a boundary that is merely internal to the text block.
    changed = True
    while changed:
        changed = False
        for i, first in enumerate(result):
            if not first.get("full_line") or first.get("preserved"):
                continue
            if first["reason"] and not first["reason"].startswith("墨迹接触区域边界"):
                continue
            for second in result[i + 1:]:
                if not second.get("full_line") or second.get("preserved"):
                    continue
                if second["reason"] and not second["reason"].startswith("墨迹接触区域边界"):
                    continue
                upper, lower = sorted((first, second), key=lambda r: r["box"][1])
                a, b = fitz.Rect(upper["box"]), fitz.Rect(lower["box"])
                union = a | b
                starts_item = starts_numbered_item(lower["text"]) or any(r.get("preserved") and re.fullmatch(r"[\[(]?[A-Za-z0-9]{1,3}[).]", r["text"].strip())
                                  and a.y0 < r["box"][1] <= b.y1 and 0 <= union.x0 - r["box"][2] < 35
                                  for r in result)
                if (-1 <= b.y0 - a.y1 <= 5 and min(a.x1, b.x1) - max(a.x0, b.x0) > min(a.width, b.width) * .7
                        and not starts_item
                        and not any(r is not first and r is not second and union.intersects(fitz.Rect(r["box"])) for r in result)):
                    merged = dict(first, box=list(union), text=upper["text"] + " " + lower["text"],
                                  lines=upper["lines"] + lower["lines"], reason="")
                    _, reason = ink_patch(page, merged)
                    if not reason:
                        result[i] = merged
                        result.remove(second)
                        changed = True
                        break
            if changed:
                break
    for region in result:
        if region.get("recovered"):
            continue
        if any(r is not region and fitz.Rect(region["box"]).intersects(fitz.Rect(r["box"])) for r in result):
            tight = recovery.tight_box(region["box"], page.rect)
            if tight:
                region["box"] = tight
    for region in result:
        if len(region.get("lines", [])) != 1:
            continue
        box = fitz.Rect(region["box"])
        matches = [row for row, _ in recovery.native_rows
                   if abs(row.y0 + row.y1 - box.y0 - box.y1) / 2 < min(row.height, box.height) * .4
                   and (row & box).width > min(row.width, box.width) * .8]
        if matches:
            nearest = min(matches, key=lambda r: abs(r.y0+r.y1-box.y0-box.y1))
            region["source_row"] = tuple(round(v, 2) for v in nearest)
    result = discard_duplicate_observations(result)
    separate_verified_rows(result)
    for region in result:
        if not region.get("reason") and any(
                r is not region and fitz.Rect(region["box"]).intersects(fitz.Rect(r["box"])) for r in result):
            region["reason"] = "局部识别后排版框仍与相邻内容相交"
    return result
