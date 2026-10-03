"""ZFIN fish components: the fish -> allele/reagent -> gene links the KG lacks.

The Monarch KG has no nodes for ZFIN sequence-targeting reagents (morpholinos,
CRISPRs, TALENs), so fish built from reagents reach no gene there. ZFIN's
`fish_components_fish.txt` download lists every fish's components with the gene
each one affects. `sync_zfin` copies it with provenance; `ZfinComponents`
indexes it for the build.
"""

from __future__ import annotations

import gzip
import shutil
import urllib.request
from collections import defaultdict
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from .common import load_json, now, replace_directory, sha256, write_json

FISH_COMPONENTS_URL = "https://zfin.org/downloads/fish_components_fish.txt"
FISH_COMPONENTS_FILE = "fish_components_fish.txt.gz"
FISH_COMPONENT_COLUMNS = (
    "fish_id",
    "fish_name",
    "gene_id",
    "gene_symbol",
    "affector_id",
    "affector_name",
    "construct_id",
    "construct_name",
    "background_id",
    "background_name",
    "genotype_id",
    "genotype_name",
)

# ZFIN id infixes for affectors that are reagents rather than alleles.
REAGENT_TYPES = {"MRPHLNO": "MORPHOLINO", "CRISPR": "CRISPR", "TALEN": "TALEN"}


def release_date(last_modified: str | None, retrieved_at: str) -> str:
    """ZFIN curates live and regenerates downloads daily, so a file's date is its release."""
    if last_modified:
        try:
            return parsedate_to_datetime(last_modified).date().isoformat()
        except (TypeError, ValueError):
            pass
    return retrieved_at[:10]


def sync_zfin(output: Path, url: str = FISH_COMPONENTS_URL) -> dict[str, Any]:
    output = output.expanduser().resolve()
    replace_directory(output)
    target = output / FISH_COMPONENTS_FILE
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - fixed https URL
        last_modified = response.headers.get("Last-Modified")
        with gzip.open(target, "wb") as stream:
            shutil.copyfileobj(response, stream)
    with gzip.open(target, "rt", encoding="utf-8") as stream:
        rows = sum(1 for line in stream if line.strip())
    retrieved_at = now()
    manifest = {
        "schema_version": 1,
        "retrieved_at": retrieved_at,
        "version": release_date(last_modified, retrieved_at),
        "url": url,
        "last_modified": last_modified,
        "files": [{"path": target.name, "rows": rows, "sha256": sha256(target)}],
    }
    write_json(output / "provenance.json", manifest)
    return manifest


def _curie(zfin_id: str) -> str | None:
    return f"ZFIN:{zfin_id}" if zfin_id else None


class ZfinComponents:
    def __init__(self, source: Path):
        manifest_path = source / "provenance.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"ZFIN provenance not found: {manifest_path}")
        self.provenance = load_json(manifest_path)
        for item in self.provenance.get("files") or []:
            if sha256(source / item["path"]) != item["sha256"]:
                raise ValueError(f"ZFIN file was modified after sync: {item['path']}")
        self.by_fish: dict[str, list[dict[str, str]]] = defaultdict(list)
        with gzip.open(source / FISH_COMPONENTS_FILE, "rt", encoding="utf-8") as stream:
            for line in stream:
                values = line.rstrip("\n").split("\t")
                if len(values) != len(FISH_COMPONENT_COLUMNS):
                    continue
                row = dict(zip(FISH_COMPONENT_COLUMNS, values, strict=True))
                self.by_fish[f"ZFIN:{row['fish_id']}"].append(row)

    def genes(self, fish_curie: str) -> dict[str, dict[str, Any]]:
        """Gene CURIE -> symbol, alleles, and reagents for one fish."""
        genes: dict[str, dict[str, Any]] = {}
        for row in self.by_fish.get(fish_curie, []):
            gene = _curie(row["gene_id"])
            if not gene:
                continue
            entry = genes.setdefault(
                gene, {"symbol": row["gene_symbol"], "alleles": [], "reagents": []}
            )
            affector = row["affector_id"]
            infix = affector.split("-")[1] if affector.count("-") >= 2 else ""
            item = {"id": _curie(affector), "label": row["affector_name"]}
            if infix in REAGENT_TYPES:
                item["reagent_type"] = REAGENT_TYPES[infix]
                if item not in entry["reagents"]:
                    entry["reagents"].append(item)
            elif affector and item not in entry["alleles"]:
                entry["alleles"].append(item)
        return genes
