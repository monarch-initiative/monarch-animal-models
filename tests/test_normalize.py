import unittest

from animal_models_browser.normalize import (
    normalize_category,
    normalize_species,
    plain_genotype_label,
)
from animal_models_browser.zfin_source import release_date


class SpeciesTests(unittest.TestCase):
    def test_common_and_scientific_names_map_to_one_taxon(self):
        for value in (
            "Mouse",
            "mouse",
            "Mus musculus",
            "Mouse (Mus musculus)",
            "Mus musculus (mouse)",
        ):
            result = normalize_species(value)
            self.assertEqual(result["species"]["id"], "NCBITaxon:10090", value)
            self.assertEqual(result["species_basis"], "SAFE_MAPPING")
            self.assertEqual(result["species_as_curated"], value)

    def test_multi_species_and_unknown_values_are_not_normalized(self):
        for value in ("Mouse and rat", "Rodents", "Human (patient-derived)", "Xenopus"):
            result = normalize_species(value)
            self.assertIsNone(result["species"], value)
            self.assertEqual(result["species_basis"], "NOT_NORMALIZED")

    def test_missing_species_is_not_reported(self):
        self.assertEqual(normalize_species(None)["species_basis"], "NOT_REPORTED")
        self.assertEqual(normalize_species("  ")["species_basis"], "NOT_REPORTED")


class CategoryTests(unittest.TestCase):
    def test_enum_style_and_prose_variants_map(self):
        self.assertEqual(normalize_category("KNOCKOUT")["model_category"], "KNOCKOUT")
        self.assertEqual(normalize_category("Knock-in model")["model_category"], "KNOCK_IN")
        self.assertEqual(
            normalize_category("Patient-derived xenograft")["model_category"], "XENOGRAFT"
        )

    def test_compound_descriptions_are_kept_raw(self):
        result = normalize_category("Knockout, dietary challenge, and biotin rescue")
        self.assertIsNone(result["model_category"])
        self.assertEqual(result["model_category_basis"], "NOT_NORMALIZED")


class GenotypeLabelTests(unittest.TestCase):
    def test_superscripts_become_angle_brackets_and_background_splits(self):
        label, background = plain_genotype_label(
            "Wdr62<sup>tm1.1Jfch</sup>/Wdr62<sup>tm1.1Jfch</sup>"
            " [background:] involves: 129S1/SvImJ"
        )
        self.assertEqual(label, "Wdr62<tm1.1Jfch>/Wdr62<tm1.1Jfch>")
        self.assertEqual(background, "involves: 129S1/SvImJ")

    def test_plain_labels_pass_through(self):
        self.assertEqual(plain_genotype_label("scn1lab<s552>"), ("scn1lab<s552>", None))


class ZfinReleaseTests(unittest.TestCase):
    def test_release_is_the_file_date(self):
        self.assertEqual(
            release_date("Fri, 02 Oct 2026 05:04:15 GMT", "2026-10-03T02:59:15Z"), "2026-10-02"
        )

    def test_release_falls_back_to_the_retrieval_date(self):
        self.assertEqual(release_date(None, "2026-10-03T02:59:15Z"), "2026-10-03")
        self.assertEqual(release_date("not a date", "2026-10-03T02:59:15Z"), "2026-10-03")


if __name__ == "__main__":
    unittest.main()
