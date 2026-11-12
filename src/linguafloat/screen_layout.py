"""Reading-order chunks and width-aware, content-preserving text lines."""
import re


def translation_chunks(text, limit=240):
    # OCR rows remain ordered. Join wrapped rows into sentences/paragraphs,
    # not one API request per tiny detector box.
    paragraphs = re.split(r"\n\s*\n", text.strip())
    result = []
    for paragraph in paragraphs:
        rows = paragraph.splitlines()
        current = ""
        for row in rows:
            row = row.strip()
            if not row:
                continue
            if current:
                joiner = "" if re.search(r"[\u3400-\u9fff]$", current) and re.match(r"[\u3400-\u9fff]", row) else " "
                if len(current) + len(row) > limit or re.search(r"[.!?。！？][\"’”']?$", current):
                    result.append(current); current = ""
                else:
                    current += joiner
            current += row
        if current:
            # Bound requests at sentence boundaries; never split a word/formula.
            parts = re.split(r"(?<=[。！？])|(?<=[.!?])\s+(?=[A-Z\u3400-\u9fff])", current)
            chunk = ""
            for part in parts:
                if chunk and len(chunk) + len(part) > limit:
                    result.append(chunk); chunk = ""
                chunk += (" " if chunk and not re.search(r"[。！？]$", chunk) else "") + part
            if chunk:
                result.append(chunk)
    return result


def readable_lines(text, width, measure):
    """Prefer sentence/phrase boundaries only if the paragraph does not fit."""
    width = max(40, width)
    output = []
    for paragraph in text.split("\n"):
        if not paragraph or measure(paragraph) <= width:
            output.append(paragraph)
            continue
        # Decimal points, abbreviations and URLs are not treated as sentences.
        sentences = re.split(r"(?<=[。！？])|(?<=[.!?])\s+(?=[A-Z\u3400-\u9fff])", paragraph)
        for sentence in sentences:
            if not sentence:
                continue
            if measure(sentence) <= width:
                output.append(sentence)
                continue
            tokens = re.findall(r"[\u3400-\u9fff]|[^\s\u3400-\u9fff]+\s*|\s+", sentence)
            lines = []; line = ""
            for token in tokens:
                if line and measure((line + token).rstrip()) > width:
                    # A nearby comma/semicolon is a better break than a word.
                    punctuation = list(re.finditer(r"[,;:，；：]\s*", line))
                    split = punctuation[-1].end() if punctuation else 0
                    if split and measure(line[:split]) >= width * .6:
                        lines.append(line[:split].rstrip()); line = line[split:]
                    else:
                        lines.append(line.rstrip()); line = ""
                line += token
            if line:
                lines.append(line.rstrip())
            if len(lines) > 1 and measure(lines[-1]) < width * .455:
                words = lines[-2].split(" ")
                while len(words) > 1 and measure(lines[-1]) < width * .45:
                    candidate = words[-1] + " " + lines[-1]
                    if measure(candidate) > width:
                        break
                    lines[-1] = candidate; words.pop()
                lines[-2] = " ".join(words)
            output.extend(lines)
    return output
