"""Documented safe mappings from DisMech free text to normalized values.

These tables are the only place this repository turns a curated string into a
normalized value. Matching is exact on a lightly normalized key (case, spacing,
and a parenthetical alternative name), never a substring or pattern match, so a
new or unusual value stays visible as "not normalized" instead of being guessed.
Multi-species values such as "Mouse and rat" are deliberately unmapped.
"""

from __future__ import annotations

import re
from typing import Any

# key -> (NCBITaxon id, label). Keys are compared after `_key()`.
SPECIES_MAP: dict[str, tuple[str, str]] = {
    "mouse": ("NCBITaxon:10090", "Mus musculus"),
    "mus musculus": ("NCBITaxon:10090", "Mus musculus"),
    "rat": ("NCBITaxon:10116", "Rattus norvegicus"),
    "rattus norvegicus": ("NCBITaxon:10116", "Rattus norvegicus"),
    "zebrafish": ("NCBITaxon:7955", "Danio rerio"),
    "danio rerio": ("NCBITaxon:7955", "Danio rerio"),
    "fruit fly": ("NCBITaxon:7227", "Drosophila melanogaster"),
    "drosophila melanogaster": ("NCBITaxon:7227", "Drosophila melanogaster"),
    "caenorhabditis elegans": ("NCBITaxon:6239", "Caenorhabditis elegans"),
    "c. elegans": ("NCBITaxon:6239", "Caenorhabditis elegans"),
    "roundworm": ("NCBITaxon:6239", "Caenorhabditis elegans"),
    "nematode": ("NCBITaxon:6239", "Caenorhabditis elegans"),
    "xenopus laevis": ("NCBITaxon:8355", "Xenopus laevis"),
    "african clawed frog": ("NCBITaxon:8355", "Xenopus laevis"),
    "xenopus tropicalis": ("NCBITaxon:8364", "Xenopus tropicalis"),
    "dog": ("NCBITaxon:9615", "Canis lupus familiaris"),
    "canis lupus familiaris": ("NCBITaxon:9615", "Canis lupus familiaris"),
    "canis familiaris": ("NCBITaxon:9615", "Canis lupus familiaris"),
    "cat": ("NCBITaxon:9685", "Felis catus"),
    "felis catus": ("NCBITaxon:9685", "Felis catus"),
    "pig": ("NCBITaxon:9823", "Sus scrofa"),
    "sus scrofa": ("NCBITaxon:9823", "Sus scrofa"),
    "domestic swine": ("NCBITaxon:9823", "Sus scrofa"),
    "sheep": ("NCBITaxon:9940", "Ovis aries"),
    "ovis aries": ("NCBITaxon:9940", "Ovis aries"),
    "goat": ("NCBITaxon:9925", "Capra hircus"),
    "capra hircus": ("NCBITaxon:9925", "Capra hircus"),
    "cattle": ("NCBITaxon:9913", "Bos taurus"),
    "bos taurus": ("NCBITaxon:9913", "Bos taurus"),
    "horse": ("NCBITaxon:9796", "Equus caballus"),
    "equus caballus": ("NCBITaxon:9796", "Equus caballus"),
    "rabbit": ("NCBITaxon:9986", "Oryctolagus cuniculus"),
    "oryctolagus cuniculus": ("NCBITaxon:9986", "Oryctolagus cuniculus"),
    "guinea pig": ("NCBITaxon:10141", "Cavia porcellus"),
    "cavia porcellus": ("NCBITaxon:10141", "Cavia porcellus"),
    "chicken": ("NCBITaxon:9031", "Gallus gallus"),
    "gallus gallus": ("NCBITaxon:9031", "Gallus gallus"),
    "rhesus macaque": ("NCBITaxon:9544", "Macaca mulatta"),
    "rhesus monkey": ("NCBITaxon:9544", "Macaca mulatta"),
    "macaca mulatta": ("NCBITaxon:9544", "Macaca mulatta"),
    "cynomolgus macaque": ("NCBITaxon:9541", "Macaca fascicularis"),
    "cynomolgus monkey": ("NCBITaxon:9541", "Macaca fascicularis"),
    "macaca fascicularis": ("NCBITaxon:9541", "Macaca fascicularis"),
    "naked mole-rat": ("NCBITaxon:10181", "Heterocephalus glaber"),
    "turquoise killifish": ("NCBITaxon:105023", "Nothobranchius furzeri"),
    "nothobranchius furzeri": ("NCBITaxon:105023", "Nothobranchius furzeri"),
    "medaka": ("NCBITaxon:8090", "Oryzias latipes"),
    "japanese medaka": ("NCBITaxon:8090", "Oryzias latipes"),
    "oryzias latipes": ("NCBITaxon:8090", "Oryzias latipes"),
    "golden hamster": ("NCBITaxon:10036", "Mesocricetus auratus"),
    "syrian hamster": ("NCBITaxon:10036", "Mesocricetus auratus"),
    "mesocricetus auratus": ("NCBITaxon:10036", "Mesocricetus auratus"),
    "ferret": ("NCBITaxon:9669", "Mustela putorius furo"),
    "mustela putorius furo": ("NCBITaxon:9669", "Mustela putorius furo"),
    "saccharomyces cerevisiae": ("NCBITaxon:4932", "Saccharomyces cerevisiae"),
}

# key -> ModelCategory. Only values that name one category unambiguously.
CATEGORY_MAP: dict[str, str] = {
    "knockout": "KNOCKOUT",
    "knockout mouse": "KNOCKOUT",
    "knockout model": "KNOCKOUT",
    "knockout mouse model": "KNOCKOUT",
    "constitutive knockout": "KNOCKOUT",
    "targeted knockout": "KNOCKOUT",
    "engineered knockout": "KNOCKOUT",
    "germline null": "KNOCKOUT",
    "conditional knockout": "CONDITIONAL_KNOCKOUT",
    "conditional knockout model": "CONDITIONAL_KNOCKOUT",
    "conditional knockout mouse model": "CONDITIONAL_KNOCKOUT",
    "knock-in": "KNOCK_IN",
    "knock in": "KNOCK_IN",
    "knockin": "KNOCK_IN",
    "knock-in model": "KNOCK_IN",
    "knock-in mouse model": "KNOCK_IN",
    "transgenic": "TRANSGENIC",
    "transgenic model": "TRANSGENIC",
    "transgenic mouse model": "TRANSGENIC",
    "knockdown": "KNOCKDOWN",
    "morpholino knockdown": "KNOCKDOWN",
    "enu-induced mutation": "CHEMICAL_MUTAGENESIS",
    "spontaneous": "SPONTANEOUS",
    "spontaneous mutation": "SPONTANEOUS",
    "natural": "SPONTANEOUS",
    "natural disease": "SPONTANEOUS",
    "naturally occurring": "SPONTANEOUS",
    "genetic": "GENETIC_UNSPECIFIED",
    "genetic model": "GENETIC_UNSPECIFIED",
    "genetically engineered": "GENETIC_UNSPECIFIED",
    "induced": "INDUCED",
    "chemically induced": "INDUCED",
    "chemical": "INDUCED",
    "infection model": "INFECTION",
    "experimental infection": "INFECTION",
    "xenograft": "XENOGRAFT",
    "patient-derived xenograft": "XENOGRAFT",
    "surgical": "SURGICAL",
}

CATEGORY_LABELS = {
    "KNOCKOUT": "Knockout",
    "CONDITIONAL_KNOCKOUT": "Conditional knockout",
    "KNOCK_IN": "Knock-in",
    "TRANSGENIC": "Transgenic",
    "KNOCKDOWN": "Knockdown",
    "CHEMICAL_MUTAGENESIS": "Chemical mutagenesis",
    "SPONTANEOUS": "Spontaneous / naturally occurring",
    "GENETIC_UNSPECIFIED": "Genetic (unspecified)",
    "INDUCED": "Induced",
    "INFECTION": "Infection",
    "XENOGRAFT": "Xenograft",
    "SURGICAL": "Surgical",
}


def _key(value: str) -> str:
    text = value.replace("_", " ").strip().casefold()
    return re.sub(r"\s+", " ", text)


def _candidate_keys(value: str) -> list[str]:
    """The whole value, then each half of a "Name (Alternative)" value."""
    keys = [_key(value)]
    match = re.fullmatch(r"\s*(.+?)\s*\((.+)\)\s*", value)
    if match:
        keys.extend([_key(match.group(1)), _key(match.group(2))])
    return keys


def normalize_species(value: Any) -> dict[str, Any]:
    raw = str(value).strip() if value not in (None, "") else None
    if not raw:
        return {"species": None, "species_as_curated": None, "species_basis": "NOT_REPORTED"}
    matches = {SPECIES_MAP[key] for key in _candidate_keys(raw) if key in SPECIES_MAP}
    if len(matches) == 1:
        taxon_id, label = matches.pop()
        return {
            "species": {"id": taxon_id, "label": label},
            "species_as_curated": raw,
            "species_basis": "SAFE_MAPPING",
        }
    return {"species": None, "species_as_curated": raw, "species_basis": "NOT_NORMALIZED"}


def normalize_category(value: Any) -> dict[str, Any]:
    raw = str(value).strip() if value not in (None, "") else None
    if not raw:
        return {
            "model_category": None,
            "model_category_as_curated": None,
            "model_category_basis": "NOT_REPORTED",
        }
    category = CATEGORY_MAP.get(_key(raw))
    if category:
        return {
            "model_category": category,
            "model_category_as_curated": raw,
            "model_category_basis": "SAFE_MAPPING",
        }
    return {
        "model_category": None,
        "model_category_as_curated": raw,
        "model_category_basis": "NOT_NORMALIZED",
    }


_SUP = re.compile(r"<sup>(.*?)</sup>", re.IGNORECASE)
_TAG = re.compile(r"</?(?:sup|sub|i|b|em|span)\b[^>]*>", re.IGNORECASE)


def plain_genotype_label(label: str) -> tuple[str, str | None]:
    """Render an MGI/ZFIN genotype label as plain text and split off its background.

    ``Wdr62<sup>tm1.1Jfch</sup>/Wdr62<sup>tm1.1Jfch</sup> [background:] involves: 129S1``
    becomes ``("Wdr62<tm1.1Jfch>/Wdr62<tm1.1Jfch>", "involves: 129S1")``.
    """
    text = _SUP.sub("\x00\\1\x01", label)
    text = _TAG.sub("", text).replace("\x00", "<").replace("\x01", ">").strip()
    background = None
    if "[background:]" in text:
        text, background = (part.strip() for part in text.split("[background:]", 1))
    return re.sub(r"\s+", " ", text), background or None
