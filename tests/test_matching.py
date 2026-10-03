import unittest
from collections import defaultdict

from animal_models_browser.matching import (
    _coverage,
    annotate_kg_precision,
    disease_relation,
    find_counterparts,
)


class FakeKG:
    """MONDO:1 > MONDO:2 > MONDO:3, plus an unrelated MONDO:9."""

    def __init__(self):
        self.ancestors = defaultdict(
            set, {"MONDO:2": {"MONDO:1"}, "MONDO:3": {"MONDO:2", "MONDO:1"}}
        )
        self.descendants = defaultdict(
            set, {"MONDO:1": {"MONDO:2", "MONDO:3"}, "MONDO:2": {"MONDO:3"}}
        )
        self.descendant_counts = {"MONDO:1": 2, "MONDO:2": 1}


def model(record_id, source, disease, *, publications=(), taxon="NCBITaxon:10090", genes=()):
    return {
        "id": record_id,
        "source": source,
        "species": {"id": taxon} if taxon else None,
        "publications": list(publications),
        "disease_associations": [{"disease": {"id": disease}}] if disease else [],
        "genetic_components": [{"human_gene": {"id": gene}} for gene in genes],
    }


class DiseaseRelationTests(unittest.TestCase):
    def test_relations_follow_subclass_direction(self):
        kg = FakeKG()
        self.assertEqual(disease_relation("MONDO:2", "MONDO:2", kg), "SAME")
        self.assertEqual(disease_relation("MONDO:2", "MONDO:3", kg), "KG_MORE_SPECIFIC")
        self.assertEqual(disease_relation("MONDO:2", "MONDO:1", kg), "KG_MORE_GENERAL")
        self.assertEqual(disease_relation("MONDO:2", "MONDO:9", kg), "UNRELATED")
        self.assertEqual(disease_relation(None, "MONDO:9", kg), "NOT_COMPARABLE")

    def test_precision_names_the_nearest_dismech_entry(self):
        kg = FakeKG()
        contexts = {"MONDO:1": [{"id": "disorder:A"}], "MONDO:2": [{"id": "disorder:B"}]}
        records = [
            model("k1", "MGI", "MONDO:3"),
            model("k2", "MGI", "MONDO:2"),
            model("k3", "MGI", "MONDO:9"),
        ]
        annotate_kg_precision(records, contexts, kg)
        first, second, third = (r["disease_associations"][0] for r in records)
        self.assertEqual(
            (first["dismech_precision"], first["dismech_entries"]),
            ("MORE_SPECIFIC", ["disorder:B"]),
        )
        self.assertEqual(
            (second["dismech_precision"], second["dismech_entries"]), ("EXACT", ["disorder:B"])
        )
        self.assertEqual(third["dismech_precision"], "NO_DISMECH_ENTRY")


class CounterpartTests(unittest.TestCase):
    def test_shared_publication_alone_is_enough(self):
        pairs = find_counterparts(
            [model("d1", "DISMECH", "MONDO:2", publications=["PMID:1"])],
            [model("k1", "MGI", "MONDO:9", publications=["PMID:1"], taxon="NCBITaxon:7955")],
            FakeKG(),
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["bases"], ["SAME_PUBLICATION"])
        self.assertEqual(pairs[0]["disease_relation"], "UNRELATED")

    def test_gene_rule_needs_species_and_a_close_disease(self):
        kg = FakeKG()
        dismech = [model("d1", "DISMECH", "MONDO:2", genes=["HGNC:1"])]
        close = model("k1", "MGI", "MONDO:3", genes=["HGNC:1"])
        other_species = model("k2", "ZFIN", "MONDO:2", genes=["HGNC:1"], taxon="NCBITaxon:7955")
        unrelated = model("k3", "MGI", "MONDO:9", genes=["HGNC:1"])
        pairs = find_counterparts(dismech, [close, other_species, unrelated], kg)
        self.assertEqual([p["kg_model_id"] for p in pairs], ["k1"])
        self.assertEqual(pairs[0]["bases"], ["RELATED_DISEASE", "SAME_SPECIES", "SAME_HUMAN_GENE"])


class CoverageTests(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(_coverage(True, True, False, False), "BOTH")
        self.assertEqual(_coverage(True, False, True, False), "DISMECH_AND_KG_OTHER_PRECISION")
        self.assertEqual(_coverage(True, False, False, False), "DISMECH_ONLY")
        self.assertEqual(_coverage(False, True, True, True), "KG_ONLY")
        self.assertEqual(_coverage(False, False, True, True), "KG_SUBTYPE_ONLY")
        self.assertEqual(_coverage(False, False, False, True), "KG_CLOSE_PARENT_ONLY")
        self.assertEqual(_coverage(False, False, False, False), "NONE")


if __name__ == "__main__":
    unittest.main()
