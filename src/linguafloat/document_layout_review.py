"""Local diagnostics on the saved page, independent of translation eligibility."""
from collections import defaultdict


def ink_conflicts(page, panel):
    from pymupdf import Rect

    cells = defaultdict(list)
    collisions = defaultdict(list)
    outside = []
    for i, span in enumerate(page.get_texttrace()):
        if span['type'] not in (0, 1) or span['opacity'] <= .05 or span['dir'][0] < .99:
            continue
        for code, _, point, bounds in span['chars']:
            if not chr(code).isalnum():
                continue
            box = Rect(bounds)
            if not panel.contains(point) or box.is_empty:
                continue
            if (box & panel).get_area() < .65 * box.get_area():
                outside.append(box)
            x, y = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
            # Compare the interiors of glyphs rather than font ascenders and
            # descenders. Ordinary adjacent lines and superscripts may touch.
            core = Rect(x-box.width*.32, y-box.height*.23, x+box.width*.32, y+box.height*.23)
            keys = [(a, b) for a in range(int(core.x0//9), int(core.x1//9)+1)
                    for b in range(int(core.y0//9), int(core.y1//9)+1)]
            matched = set()
            for key in keys:
                for j, previous, old_seq, old_type in cells[key]:
                    if j == i or j in matched or (abs(old_seq-span['seqno']) <= 1 and span['type'] != old_type):
                        continue
                    if abs((previous.y0+previous.y1)/2-y) > max(2, box.height*.36):
                        continue
                    if (core & previous).get_area() > .35*min(core.get_area(), previous.get_area()):
                        collisions[j, i].append(box)
                        matched.add(j)
                cells[key].append((i, core, span['seqno'], span['type']))
    regions = defaultdict(dict)
    for boxes in collisions.values():
        for box in boxes:
            regions[int(box.y0//20)][tuple(box)] = box
    overlap = next((list(boxes.values()) for boxes in regions.values() if len(boxes) >= 4), ())
    return overlap, outside, bool(cells)


def findings(source, saved, panel, reference_areas=(), *, aligned=False):
    from pymupdf import Rect

    overlap, outside, has_ink = ink_conflicts(saved, panel)
    offset = (panel.x0, panel.y0, panel.x0, panel.y0)

    def issue(kind, status, box, evidence, original='', target=''):
        return dict(kind=kind, status=status, source_page=source.number+1, pdf_page=saved.number+1,
                    box=list(box), evidence=evidence, source=original, target=target)

    if overlap:
        box = Rect(overlap[0])
        for part in overlap[1:]:
            box |= part
        yield issue('layout-overlap', 'suspected', box,
                    '不同文字绘制指令的多个字形相互覆盖；请核对重叠或重复文字。',
                    target=saved.get_textbox((box + (-12, -4, 46, 4)) & panel).strip())
    if len(outside) >= 3:
        yield issue('layout-clip', 'suspected', outside[0], '文字字形越过译文面边界，可能发生裁切。')
    if not aligned:
        return
    if reference_areas:
        links = saved.get_links()
    for area in reference_areas:
        box = Rect(area) + offset
        original = source.get_textbox(Rect(area)).strip()
        target = saved.get_textbox(box).strip()
        compact = lambda text: ''.join(text.split())
        if original and compact(original) != compact(target):
            yield issue('layout-reference', 'confirmed', box,
                        '应原样保留的参考文献区域与原文的可复制文字不一致；可能存在错位、重复绘制或裁切。',
                        original[:450], target[:450])
        wrong = [item for item in source.get_links() if item.get('uri') and Rect(area).contains(item['from'])
                 and not any(link.get('uri') == item['uri'] and
                             ((Rect(item['from']) + offset) & link['from']).get_area() >= .7*Rect(item['from']).get_area()
                             for link in links)]
        if wrong:
            yield issue('layout-link', 'confirmed', Rect(wrong[0]['from']) + offset,
                        f'此参考文献区域有 {len(wrong)} 处原链接在对应位置缺失或目标不符。',
                        wrong[0]['uri'])
