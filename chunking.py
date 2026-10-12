"""Keep section boundaries and ancestry; recursively split only within a section."""
import re
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]


def heading(line):
    markdown = re.fullmatch(r"(#{1,6})\s+(.+?)\s*#*", line.strip())
    if markdown:
        return len(markdown[1]), markdown[2].strip()
    # Long prose beginning with a chapter reference is not itself a title.
    if len(line.strip()) > 120:
        return None
    chinese = re.fullmatch(r"第[零〇一二三四五六七八九十百千万\d]+([章节篇部])\s*.+", line.strip())
    if chinese:
        return (2 if chinese[1] == "节" else 1), line.strip()
    numbered = re.fullmatch(r"(\d+(?:\.\d+){1,4})[ \t]+\S.+", line.strip())
    if numbered:
        return min(numbered[1].count(".") + 1, 6), line.strip()
    enumeration = re.fullmatch(r"[一二三四五六七八九十]+[、．]\s*\S.+", line.strip())
    if enumeration:
        return 2, line.strip()
    return None


def sections(text, initial_stack=()):
    """Return exact body offsets; ignore headings inside Markdown code fences."""
    stack, result, start, offset, fence = list(initial_stack), [], 0, 0, None
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        marker = re.match(r"^(`{3,}|~{3,})", stripped)
        if marker:
            if fence is None:
                fence = (marker[1][0], len(marker[1]))
            elif marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not stripped[len(marker[1]):].strip():
                fence = None
            offset += len(line)
            continue
        found = heading(line) if fence is None else None
        if found:
            if text[start:offset].strip():
                result.append((start, text[start:offset], " > ".join(title for _, title in stack)))
            level, title = found
            stack = [(old_level, old_title) for old_level, old_title in stack if old_level < level]
            stack.append((level, title))
            start = offset + len(line)
        offset += len(line)
    if text[start:].strip():
        result.append((start, text[start:], " > ".join(title for _, title in stack)))
    return result, stack


def split_documents(docs, chunk_size=800, chunk_overlap=120, strategy="structured"):
    if strategy not in {"structured", "recursive"}:
        raise ValueError("切分策略应为 structured 或 recursive")
    if chunk_size < 50 or not 0 <= chunk_overlap < chunk_size:
        raise ValueError("要求 chunk_size >= 50 且 0 <= overlap < size")
    chunks, stack, previous = [], [], None
    for doc in docs:
        scope = (doc.metadata.get('filename'), doc.metadata.get('page', 0))
        if strategy == "structured":
            continuation = previous and scope[0] == previous[0] and scope[1] and scope[1] == previous[1] + 1
            pieces, stack = sections(doc.page_content, stack if continuation else ())
        else:
            pieces = [(0, doc.page_content, "")]
        previous = scope
        for offset, body, path in pieces:
            # Repeating the heading path gives every long-section fragment its subject.
            prefix = (path[:chunk_size // 3] + "\n\n") if path else ""
            budget = chunk_size - len(prefix)
            splitter = RecursiveCharacterTextSplitter(chunk_size=budget,
                chunk_overlap=min(chunk_overlap, budget - 1), separators=SEPARATORS, add_start_index=True)
            metadata = {**doc.metadata, "section_path": path, "split_strategy": strategy}
            for chunk in splitter.split_documents([Document(page_content=body, metadata=metadata)]):
                if chunk.page_content.strip():
                    chunk.metadata["start_index"] += offset
                    chunk.page_content = prefix + chunk.page_content
                    chunks.append(chunk)
    return chunks
