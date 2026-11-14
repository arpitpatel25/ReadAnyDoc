"""PDF link annotations and comments -> blocks (``formatting.include``: hyperlinks, comments).

Intake stores what it read from the PDF in ``page.properties["annotations"]``:
``{"links": [{"bbox": {...}, "url": str}], "comments": [{"bbox": {...}, "text": str, "author": str}]}``
with boxes normalised to the page. Here links are attached to the grounded words they cover
(``region.meta["links"]``, character spans into ``region.content``) and comments become Comment blocks.
"""

from __future__ import annotations

from ...model import BBox, Confidence, Document, Region, RegionType


def attach_links(doc: Document) -> int:
    n = 0
    for page in doc.pages:
        links = (page.properties.get("annotations") or {}).get("links") or []
        if not links:
            continue
        for region in page.regions:
            if region.type == RegionType.TABLE:
                continue  # offsets inside table HTML are not safe to wrap
            words = [w for w in region.iter_words() if w.bbox is not None and w.char_start is not None
                     and w.char_end is not None]
            if not words:
                continue
            found = []
            for ln in links:
                box = BBox(**{**ln["bbox"], "page": page.index})
                inside = [w for w in words if box.contains_point(*w.bbox.center, pad=0.002)]
                if not inside:
                    continue
                inside.sort(key=lambda w: w.char_start)
                runs, cur = [], [inside[0]]
                for w in inside[1:]:  # one link can wrap onto a second line: keep contiguous runs apart
                    gap = region.content[cur[-1].char_end:w.char_start]
                    if gap.strip() == "" and "\n" not in gap:
                        cur.append(w)
                    else:
                        runs.append(cur)
                        cur = [w]
                runs.append(cur)
                for run in runs:
                    found.append({"start": run[0].char_start, "end": run[-1].char_end, "url": ln["url"]})
            if found:
                region.meta["links"] = found
                n += len(found)
    return n


def add_comments(doc: Document) -> int:
    n = 0
    for page in doc.pages:
        comments = (page.properties.get("annotations") or {}).get("comments") or []
        have = {r.meta.get("annotation_id") for r in page.regions}
        for i, c in enumerate(comments):
            key = f"p{page.index}c{i}"
            if key in have or not (c.get("text") or "").strip():
                continue
            text = c["text"].strip()
            if c.get("author"):
                text = f"{c['author']}: {text}"
            box = BBox(**{**c["bbox"], "page": page.index}) if c.get("bbox") else None
            page.regions.append(Region(type=RegionType.COMMENT, content=text, text=text, bbox=box,
                                       order=len(page.regions), source="pdf:annotation",
                                       confidence=Confidence(score=1.0, label="high"),
                                       meta={"annotation_id": key, "subtype": c.get("subtype")}))
            n += 1
    return n
