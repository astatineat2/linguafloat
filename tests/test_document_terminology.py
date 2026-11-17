"""Document-local evidence is auditable context, never a canonical glossary."""
import json
import socket

import pytest

from linguafloat.document_context import (
    CONTEXT_LIMIT, GraphicContext, SourceBlock, SourceContext, acronym_definitions,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Terminology tests must not open network connections")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


def test_definition_export_keeps_literal_sources_locations_and_ambiguity():
    blocks = [
        SourceBlock("Endoplasmic reticulum (ER).", "references", "Excluded"),
        SourceBlock("Indole-3-lactic acid (ILA) is produced by Lactiplantibacillus plantarum DX7.", section="Results"),
        SourceBlock("Indole-3-lactic acid (ILA) increased.", "figure_caption", "Results"),
        SourceBlock("Endoplasmic reticulum (ER) was observed.", section="Cells"),
        SourceBlock("Estrogen receptor (ER) was measured.", section="Receptors"),
    ]
    context = SourceContext(blocks)
    exported = context.export_evidence()
    assert exported["status"] == "candidate"
    ila = [e for e in exported["definitions"] if e["abbreviation"] == "ILA"]
    assert len(ila) == 1 and ila[0]["source_definition"] == "Indole-3-lactic acid"
    assert ila[0]["status"] == "candidate" and not ila[0]["ambiguous"]
    assert [p["block_index"] for p in ila[0]["source_paragraphs"]] == [1, 2]
    assert ila[0]["source_paragraphs"][0]["source"] == blocks[1].text
    assert ila[0]["source_paragraphs"][1]["role"] == "figure_caption"
    er = [e for e in exported["definitions"] if e["abbreviation"] == "ER"]
    assert len(er) == 2 and all(e["ambiguous"] for e in er)
    assert all(p["section"] != "Excluded" for e in er for p in e["source_paragraphs"])
    assert context.packet("ER response")["document_definitions"] == [{"abbreviation": "ER", "ambiguous": True}]
    assert context.blocks[0] is blocks[1]
    exported["definitions"][0]["source_paragraphs"].clear()
    assert context.export_evidence()["definitions"][0]["source_paragraphs"]
    assert SourceContext().export_evidence()["definitions"] == []


@pytest.mark.parametrize("label,expected", [
    ("ILA/DX7", True), ("ILA-producing", True), ("中文ILA含量", True),
    ("ILAs", False), ("MILA", False), ("ILA2", False), ("I LA", False),
])
def test_acronym_matching_keeps_mixed_script_boundaries_without_guessing(label, expected):
    context = SourceContext([SourceBlock("Indole-3-lactic acid (ILA) was measured.")])
    assert bool(context.packet(label).get("document_definitions")) == expected
    assert list(acronym_definitions("Lactiplantibacillus plantarum (DX7)")) == []


def test_completed_pairs_export_is_candidate_only_and_restorable():
    context = GraphicContext()
    source = "Lactiplantibacillus plantarum DX7 produced ILA."
    context.remember(4, source, "Lactiplantibacillus plantarum DX7 产生 ILA。", "figure_caption")
    context.remember(4, source, "Lactiplantibacillus plantarum DX7 产生 ILA。", "figure_caption")
    context.remember(4, "Excluded", "不可学习", "references")
    evidence = context.export_evidence()["completed_prose"]
    assert len(evidence) == 1
    assert evidence[0]["source"] == source and evidence[0]["page"] == 5
    assert evidence[0]["status"] == "candidate" and not evidence[0]["ambiguous"]
    restored = GraphicContext()
    restored.restore(json.loads(json.dumps(context.records)))
    assert restored.export_evidence() == context.export_evidence()
    evidence[0]["translation"] = "mutated"
    assert context.export_evidence()["completed_prose"][0]["translation"] != "mutated"
    assert GraphicContext().export_evidence()["completed_prose"] == []


@pytest.mark.parametrize("label,expected", [("DX7", True), ("DX8", False), ("DX70", False),
                                           ("HER2", False), ("HER3", True), ("dx7", False),
                                           ("DX-7", True), ("DX-8", False),
                                           ("DX7-A", True), ("DX7-B", False)])
def test_graphic_tokens_preserve_identifier_identity(label, expected):
    context = GraphicContext()
    context.remember(0, "DX7 and HER3 and DX-7 and DX7-A were measured.", "检测这些标识。", "body")
    assert bool(context.packet(0, label, "")["completed_prose"]) == expected


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_completed_pairs_never_make_first_translation_canonical(reverse):
    context = GraphicContext()
    translations = ["质量下降", "质量升高"]
    for translation in reversed(translations) if reverse else translations:
        context.remember(0, "Shield mass reduction", translation, "body")
    packet = context.packet(0, "Shield mass reduction", "")
    assert packet["completed_prose"] == []
    assert packet["ambiguous_sources"]
    exported = context.export_evidence()["completed_prose"]
    assert {e["translation"] for e in exported} == set(translations)
    assert all(e["ambiguous"] and e["status"] == "candidate" for e in exported)


def test_graphic_acronym_ambiguity_is_not_silently_resolved_by_proximity():
    context = GraphicContext()
    context.remember(0, "Endoplasmic reticulum (ER) response.", "内质网反应。", "body")
    context.remember(4, "Estrogen receptor (ER) response.", "雌激素受体反应。", "caption")
    context.remember(0, "Endoplasmic reticulum response.", "内质网反应。", "body")
    packet = context.packet(0, "ER response", "Figure 1. ER response.")
    assert packet["completed_prose"] == []
    assert packet["document_definitions"] == [{"abbreviation": "ER", "ambiguous": True}]


def test_context_remains_bounded_and_uses_caption_as_evidence_not_a_target():
    context = GraphicContext()
    context.remember(2, "DX7 growth " + "α" * 2000, "DX7 生长" * 500, "body")
    context.remember(2, "DX7 production " + "β" * 2000, "DX7 产量" * 500, "caption")
    packet = context.packet(2, "DX7", "Figure 2. " + "γ" * 2000)
    assert len(json.dumps(packet, ensure_ascii=False)) <= CONTEXT_LIMIT
    assert packet["status"] == "candidate"
    assert len(packet["completed_prose"]) <= 2
    assert packet["caption_source"].startswith("Figure 2.")
    assert all(e["status"] == "candidate" for e in packet["completed_prose"])


def test_neighboring_definition_does_not_choose_an_ambiguous_acronym():
    context = SourceContext([
        SourceBlock("Endoplasmic reticulum (ER).", section="A"),
        SourceBlock("ER response.", section="A"),
        SourceBlock("Estrogen receptor (ER).", section="B"),
    ])
    packet = context.packet("ER response.")
    assert packet["status"] == "candidate"
    assert packet["document_definitions"] == [{"abbreviation": "ER", "ambiguous": True}]
    assert "preceding_source" not in packet  # Avoid privileging this definition by adjacency.


def test_ambiguity_is_not_lost_after_definition_packet_limit():
    blocks = [SourceBlock(f"{c}lpha {c.lower()}lpha ({c}{c}).") for c in "ABCDEFGH"]
    query = "AA BB CC DD EE FF GG HH ZZ response."
    blocks += [SourceBlock("Zinc zone (ZZ).", section="A"), SourceBlock(query, section="A"),
               SourceBlock("Zebra zone (ZZ).", section="B")]
    context = SourceContext(blocks)
    packet = context.packet(query)
    assert {"abbreviation": "ZZ", "ambiguous": True} in packet["document_definitions"]
    assert "preceding_source" not in packet
    assert len(packet["document_definitions"]) <= 8
