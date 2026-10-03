import csv
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from animal_models_browser.common import sha256, write_json
from animal_models_browser.dismech_source import sync_dismech
from animal_models_browser.kg_source import SLICE_QUERIES
from animal_models_browser.pipeline import build_site, validate_site
from animal_models_browser.zfin_source import FISH_COMPONENTS_FILE

FIXTURES = Path(__file__).parent / "fixtures"

KG_ROWS = {
    "model_of": [
        # More specific than Disease A, same publication and gene as its DisMech model.
        {
            "genotype": "MGI:100",
            "disease": "MONDO:0000003",
            "original_disease": "DOID:3",
            "source": "infores:mgi",
            "publications": "PMID:1",
            "evidence_codes": "ECO:0000033",
            "negated": "False",
        },
        # Exactly Disease B, which has no DisMech models.
        {
            "genotype": "MGI:200",
            "disease": "MONDO:0000004",
            "original_disease": "DOID:4",
            "source": "infores:mgi",
            "publications": "PMID:2",
            "evidence_codes": "",
            "negated": "False",
        },
        # A disease with no DisMech entry at any level.
        {
            "genotype": "ZFIN:ZDB-FISH-1",
            "disease": "MONDO:0000009",
            "original_disease": "MONDO:0000009",
            "source": "infores:zfin",
            "publications": "PMID:3",
            "evidence_codes": "",
            "negated": "False",
        },
        {
            "genotype": "MGI:999",
            "disease": "MONDO:0000009",
            "original_disease": "",
            "source": "infores:mgi",
            "publications": "",
            "evidence_codes": "",
            "negated": "True",
        },
    ],
    "genotypes": [
        {
            "id": "MGI:100",
            "name": "Gene1<sup>tm1</sup>/Gene1<sup>tm1</sup> [background:] B6",
            "in_taxon": "NCBITaxon:10090",
            "in_taxon_label": "Mus musculus",
        },
        {
            "id": "MGI:200",
            "name": "Gene2<sup>tm1</sup>/Gene2<sup>+</sup>",
            "in_taxon": "NCBITaxon:10090",
            "in_taxon_label": "Mus musculus",
        },
        {
            "id": "ZFIN:ZDB-FISH-1",
            "name": "gene3<sup>z1</sup>/gene3<sup>z1</sup>",
            "in_taxon": "NCBITaxon:7955",
            "in_taxon_label": "Danio rerio",
        },
    ],
    "genotype_variants": [
        {"genotype": "MGI:100", "variant": "MGI:101", "variant_label": "Gene1<sup>tm1</sup>"},
        {"genotype": "MGI:200", "variant": "MGI:201", "variant_label": "Gene2<sup>tm1</sup>"},
    ],
    "variant_genes": [{"variant": "MGI:101", "gene": "MGI:10"}],
    "genotype_genes": [],
    "orthologs": [
        {
            "human_gene": "HGNC:1",
            "model_gene": "MGI:10",
            "source": "infores:panther",
            "evidence": "PANTHER.FAMILY:PTHR1",
        },
    ],
    "genes": [
        {
            "id": "HGNC:1",
            "symbol": "GENE1",
            "name": "gene 1",
            "in_taxon": "NCBITaxon:9606",
            "in_taxon_label": "Homo sapiens",
        },
        {
            "id": "MGI:10",
            "symbol": "Gene1",
            "name": "gene 1",
            "in_taxon": "NCBITaxon:10090",
            "in_taxon_label": "Mus musculus",
        },
    ],
    "disease_closure": [
        {"child": "MONDO:0000002", "ancestor": "MONDO:0000001"},
        {"child": "MONDO:0000003", "ancestor": "MONDO:0000002"},
        {"child": "MONDO:0000003", "ancestor": "MONDO:0000001"},
    ],
    # MONDO:0000001 plays a harrisons_view area; groups stop below it.
    "disease_areas": [{"id": "MONDO:0000001", "name": "disease 1"}],
    "diseases": [{"id": f"MONDO:000000{n}", "name": f"disease {n}"} for n in (1, 2, 3, 4, 9)],
}


def write_kg_slice(path: Path) -> None:
    path.mkdir(parents=True)
    files = []
    for name in SLICE_QUERIES:
        rows = KG_ROWS[name]
        target = path / f"{name}.tsv.gz"
        with gzip.open(target, "wt", encoding="utf-8", newline="") as stream:
            fields = list(rows[0]) if rows else ["genotype", "gene"]
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        files.append({"path": target.name, "rows": len(rows), "sha256": sha256(target)})
    write_json(
        path / "provenance.json",
        {
            "schema_version": 1,
            "extracted_at": "2026-01-01T00:00:00Z",
            "location": "fixture",
            "version": "fixture",
            "files": files,
        },
    )


ZFIN_ROWS = [
    # A reagent-only fish: the KG knows no gene for it.
    [
        "ZDB-FISH-1",
        "gene3 fish",
        "ZDB-GENE-3",
        "gene3",
        "ZDB-MRPHLNO-1",
        "MO1-gene3",
        "",
        "",
        "",
        "",
        "ZDB-GENO-1",
        "AB",
    ],
    [
        "ZDB-FISH-1",
        "gene3 fish",
        "ZDB-GENE-3",
        "gene3",
        "ZDB-ALT-1",
        "z1",
        "",
        "",
        "",
        "",
        "ZDB-GENO-1",
        "AB",
    ],
    [
        "ZDB-FISH-2",
        "unrelated fish",
        "ZDB-GENE-9",
        "gene9",
        "ZDB-CRISPR-1",
        "CRISPR1-gene9",
        "",
        "",
        "",
        "",
        "ZDB-GENO-2",
        "AB",
    ],
]


def write_zfin(path: Path) -> None:
    path.mkdir(parents=True)
    target = path / FISH_COMPONENTS_FILE
    with gzip.open(target, "wt", encoding="utf-8") as stream:
        stream.writelines("\t".join(row) + "\n" for row in ZFIN_ROWS)
    write_json(
        path / "provenance.json",
        {
            "schema_version": 1,
            "url": "fixture",
            "last_modified": None,
            "retrieved_at": "2026-01-01T00:00:00Z",
            "files": [{"path": target.name, "rows": len(ZFIN_ROWS), "sha256": sha256(target)}],
        },
    )


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name) / "work" / "build" / "here"
        sync_dismech(FIXTURES / "dismech", root / "dismech")
        write_kg_slice(root / "kg")
        write_zfin(root / "zfin")
        cls.catalog = build_site(
            root / "dismech", root / "kg", root / "zfin", FIXTURES / "namo/namo.yaml", root / "dist"
        )
        cls.site = root / "dist"
        cls.models = {m["id"]: m for m in cls.catalog["models"]}
        cls.diseases = {d["id"]: d for d in cls.catalog["diseases"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_output_validates(self):
        self.assertEqual(validate_site(self.site)["model_count"], 5)

    def test_negated_kg_associations_are_dropped(self):
        self.assertNotIn("MGI:999", self.models)

    def test_dismech_model_has_curated_human_gene_and_derived_model_gene(self):
        model = self.models["dismech:kb/disorders/Disease_A.yaml:Gene1 knockout mouse"]
        component = model["genetic_components"][0]
        self.assertEqual(component["human_gene"]["id"], "HGNC:1")
        self.assertEqual(component["human_gene_basis"], "CURATED")
        self.assertEqual(component["model_gene"]["id"], "MGI:10")
        self.assertEqual(component["model_gene_basis"], "DERIVED")
        self.assertEqual(component["correspondence"]["support"][0]["source"], "infores:panther")
        self.assertEqual(model["species"]["id"], "NCBITaxon:10090")
        self.assertEqual(model["model_category"], "KNOCKOUT")
        hood = model["modeled_mechanisms"][0]["neighborhood"]
        self.assertEqual(hood["status"], "resolved")

    def test_unnamed_multi_species_model_keeps_raw_values(self):
        model = self.models["dismech:kb/disorders/Disease_A.yaml:Spontaneous mutant Mouse and rat"]
        self.assertEqual(model["name_basis"], "DERIVED")
        self.assertIsNone(model["species"])
        self.assertEqual(model["species_basis"], "NOT_NORMALIZED")

    def test_kg_record_shape(self):
        model = self.models["MGI:100"]
        self.assertEqual(model["name"], "Gene1<tm1>/Gene1<tm1>")
        self.assertEqual(model["strain_background"], "B6")
        association = model["disease_associations"][0]
        self.assertEqual(association["dismech_precision"], "MORE_SPECIFIC")
        self.assertEqual(association["dismech_entries"], ["disorder:Disease_A"])
        component = model["genetic_components"][0]
        self.assertEqual(
            (component["model_gene_basis"], component["human_gene_basis"]), ("CURATED", "DERIVED")
        )

    def test_zfin_components_fill_reagent_targeted_genes(self):
        model = self.models["ZFIN:ZDB-FISH-1"]
        (component,) = model["genetic_components"]
        self.assertEqual(component["model_gene"]["id"], "ZFIN:ZDB-GENE-3")
        self.assertEqual(component["model_gene"]["symbol"], "gene3")
        self.assertEqual(component["source"], "ZFIN fish components")
        self.assertEqual(component["reagents"][0]["reagent_type"], "MORPHOLINO")
        self.assertEqual([a["id"] for a in component["alleles"]], ["ZFIN:ZDB-ALT-1"])
        self.assertEqual(model["perturbation_labels"], ["Allele", "Morpholino"])
        kg_component = self.models["MGI:100"]["genetic_components"][0]
        self.assertEqual(kg_component["source"], "Monarch KG")

    def test_disease_rollups(self):
        dismech = self.models["dismech:kb/disorders/Disease_A.yaml:Gene1 knockout mouse"]
        self.assertEqual(dismech["disease_area_labels"], ["disease 1"])
        self.assertEqual(dismech["disease_group_ids"], ["MONDO:0000002"])
        # A subtype model rolls up to its parent group, never to the area.
        kg = self.models["MGI:100"]
        self.assertEqual(kg["disease_group_ids"], ["MONDO:0000002", "MONDO:0000003"])
        self.assertEqual(self.catalog["disease_groups"]["MONDO:0000002"], "disease 2")
        self.assertNotIn("MONDO:0000001", self.catalog["disease_groups"])

    def test_counterpart_lists_every_matching_basis(self):
        pairs = self.catalog["counterparts"]
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["kg_model_id"], "MGI:100")
        self.assertEqual(
            pairs[0]["bases"],
            ["SAME_PUBLICATION", "RELATED_DISEASE", "SAME_SPECIES", "SAME_HUMAN_GENE"],
        )
        self.assertEqual(pairs[0]["disease_relation"], "KG_MORE_SPECIFIC")

    def test_coverage_rows(self):
        self.assertEqual(
            self.diseases["disorder:Disease_A"]["coverage"], "DISMECH_AND_KG_OTHER_PRECISION"
        )
        self.assertEqual(self.diseases["disorder:Disease_A"]["kg_subtype_model_ids"], ["MGI:100"])
        self.assertEqual(self.diseases["disorder:Disease_B"]["coverage"], "KG_ONLY")
        self.assertEqual(self.diseases["disorder:Disease_B"]["name"], "Disease B")
        orphan = self.diseases["kg-disease:MONDO:0000009"]
        self.assertEqual((orphan["coverage"], orphan["dismech_entry"]), ("KG_ONLY", None))
        self.assertEqual(
            self.diseases["kg-disease:MONDO:0000003"]["nearest_dismech_entries"],
            ["disorder:Disease_A"],
        )
        self.assertEqual(
            self.diseases["disorder:Disease_A"]["mechanisms_recapitulated"], ["Event A"]
        )

    def test_browser_index_and_detail_shards_agree(self):
        index_text = (self.site / "data/index.js").read_text()
        index = json.loads(index_text.removeprefix("window.ANIMAL_MODELS_INDEX=").rstrip(";\n"))
        self.assertNotIn("diseases", index)
        self.assertEqual(index["dismech_entries"]["disorder:Disease_A"]["name"], "Disease A")
        for record in index["models"]:
            shard = (self.site / f"data/details/{record['detail_shard']}.js").read_text()
            self.assertIn(json.dumps(record["id"]), shard)
            self.assertNotIn("modeled_mechanisms", record)

    def test_tampered_snapshot_is_rejected(self):
        root = Path(self.tmp.name) / "work" / "build" / "tamper"
        sync_dismech(FIXTURES / "dismech", root / "dismech")
        write_kg_slice(root / "kg")
        write_zfin(root / "zfin")
        (root / "dismech/kb/disorders/Disease_B.yaml").write_text("name: changed\n")
        with self.assertRaises(ValueError):
            build_site(
                root / "dismech",
                root / "kg",
                root / "zfin",
                FIXTURES / "namo/namo.yaml",
                root / "dist",
            )


if __name__ == "__main__":
    unittest.main()
