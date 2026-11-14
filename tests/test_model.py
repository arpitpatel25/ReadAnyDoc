import json

from readanydoc.model import (BBox, Candidate, Chunk, Confidence, CrossPageLink, Document, Evidence,
                            HeadingNode, Line, Page, Region, RegionType, TableCell, Word)

from .conftest import needs_spike


def build_doc() -> Document:
    word = Word(text="Total", bbox=BBox(left=0.1, top=0.2, width=0.05, height=0.01), source="cached:haiku",
                char_start=0, char_end=5, bbox_source="ppocr", confidence=Confidence.of(0.97),
                evidence=[Evidence(source="ppocr", verdict="agree", text="Total")])
    table = Region(type=RegionType.TABLE, content="<table><tr><td>Total</td><td>5</td></tr></table>",
                   bbox=BBox(left=0.1, top=0.2, width=0.5, height=0.1), order=1, source="cached:haiku",
                   lines=[Line(text="Total", words=[word])],
                   cells=[TableCell(row=0, col=0, text="Total"), TableCell(row=0, col=1, text="5", colspan=2)],
                   candidates=[Candidate(source="cached:haiku", content="<table>...</table>"),
                               Candidate(source="layout:cached:pvl", content="Total 5")])
    title = Region(type=RegionType.TITLE, content="# Report", heading_level=1, order=0)
    page = Page(index=0, original_page=3, width=612, height=792, kind="digital", rotation=0, script="Latin",
                text_layer_quality=0.93, text_layer_usable=True, renders={"150": "/tmp/p.png"},
                words={"pdf": [word]}, regions=[title, table])
    return Document(source="x.pdf", stem="x", file_type="pdf", pages=[page],
                    heading_tree=[HeadingNode(title="Report", level=1, region_id=title.id,
                                              children=[HeadingNode(title="Sub", level=2)])],
                    links=[CrossPageLink(kind="table_continuation", from_region=table.id, to_region=table.id)],
                    chunks=[Chunk(content="# Report", embed="# Report", region_ids=[title.id, table.id])],
                    properties={"title": "Report"})


def test_round_trip_is_lossless():
    doc = build_doc()
    again = Document.from_json(doc.to_json())
    assert again == doc
    assert again.model_dump() == json.loads(json.dumps(doc.model_dump(mode="json")))
    assert again.pages[0].regions[1].cells[1].colspan == 2
    assert again.heading_tree[0].children[0].title == "Sub"


def test_bbox_helpers():
    b = BBox.from_xyxy(100, 200, 300, 400, page=2, page_width=1000, page_height=1000)
    assert (b.left, b.top, round(b.width, 6), round(b.height, 6), b.page) == (0.1, 0.2, 0.2, 0.2, 2)
    u = BBox.union([b, BBox(left=0.0, top=0.0, width=0.1, height=0.1, page=2)])
    assert (u.left, u.top, round(u.right, 6), round(u.bottom, 6)) == (0.0, 0.0, 0.3, 0.4)
    assert b.contains_point(0.2, 0.3) and not b.contains_point(0.9, 0.9)
    assert BBox.from_xyxy(-5, -5, 2000, 2000, 0, 1000, 1000).width == 1.0  # clamped
    assert BBox.union([]) is None


def test_confidence_default_is_low_until_verified():
    assert Region().confidence.label == "low"
    assert Confidence.of(0.9).label == "high" and Confidence.of(0.5).label == "low"


def test_region_types_are_reductos_twelve():
    assert len(RegionType) == 12 and RegionType.SECTION_HEADER.value == "Section Header"


@needs_spike
def test_pipeline_document_round_trip(haiku_docs):
    for doc in haiku_docs.values():
        assert Document.from_json(doc.to_json()) == doc
