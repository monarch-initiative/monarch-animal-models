"""Extract and normalize the Monarch KG slice this catalog needs.

`slice_kg` queries a Monarch KG DuckDB file -- a local path, or the published
release over HTTPS, where DuckDB reads only the pages a query touches -- and
writes a small set of gzipped TSVs plus a provenance manifest. The slice does not
depend on the DisMech snapshot, so it can be extracted once per KG release.
"""

from __future__ import annotations

import csv
import gzip
import os
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

from .common import (
    MONARCH_SITE,
    expand_curie,
    now,
    replace_directory,
    sha256,
    strings,
    write_json,
)
from .normalize import plain_genotype_label

KG_RELEASE_URL = "https://data.monarchinitiative.org/monarch-kg/latest/monarch-kg.duckdb"

MODEL_SOURCES = {"infores:mgi": "MGI", "infores:zfin": "ZFIN", "infores:rgd": "RGD"}

# Taxa whose human orthologs are kept, covering the species DisMech and the
# Alliance members use for animal models.
ORTHOLOG_TAXA = (
    "NCBITaxon:10090",  # mouse
    "NCBITaxon:10116",  # rat
    "NCBITaxon:7955",  # zebrafish
    "NCBITaxon:7227",  # fly
    "NCBITaxon:6239",  # worm
    "NCBITaxon:8355",  # Xenopus laevis
    "NCBITaxon:8364",  # Xenopus tropicalis
    "NCBITaxon:9615",  # dog
    "NCBITaxon:9823",  # pig
)

_TAXA_SQL = ", ".join(f"'{taxon}'" for taxon in ORTHOLOG_TAXA)

# Each query writes one TSV. Array columns are joined with "|".
SLICE_QUERIES: dict[str, str] = {
    "model_of": """
        select e.subject as genotype, e.object as disease, e.original_object as original_disease,
               e.primary_knowledge_source as source,
               array_to_string(e.publications, '|') as publications,
               array_to_string(e.has_evidence, '|') as evidence_codes,
               coalesce(e.negated, 'False') as negated
        from kg.edges e
        where e.predicate = 'biolink:model_of'
          and e.primary_knowledge_source in ('infores:mgi', 'infores:zfin', 'infores:rgd')
        order by 1, 2, 4
    """,
    "genotypes": """
        select n.id, n.name, n.in_taxon, n.in_taxon_label
        from kg.nodes n
        where n.id in (select subject from kg.edges where predicate = 'biolink:model_of')
        order by 1
    """,
    "genotype_variants": """
        select distinct e.subject as genotype, e.object as variant, v.name as variant_label
        from kg.edges e join kg.nodes v on v.id = e.object
        where e.predicate = 'biolink:has_sequence_variant'
          and e.subject in (select subject from kg.edges where predicate = 'biolink:model_of')
        order by 1, 2
    """,
    "variant_genes": """
        select distinct e.subject as variant, e.object as gene
        from kg.edges e
        where e.predicate = 'biolink:is_sequence_variant_of'
          and e.subject in (
            select object from kg.edges
            where predicate = 'biolink:has_sequence_variant'
              and subject in (select subject from kg.edges where predicate = 'biolink:model_of'))
        order by 1, 2
    """,
    "genotype_genes": """
        select distinct e.subject as genotype, e.object as gene
        from kg.edges e join kg.nodes g on g.id = e.object
        where e.predicate = 'biolink:related_to' and g.category = 'biolink:Gene'
          and e.subject in (select subject from kg.edges where predicate = 'biolink:model_of')
        order by 1, 2
    """,
    "orthologs": f"""
        with pairs as (
          select case when e.subject like 'HGNC:%' then e.subject else e.object end as human_gene,
                 case when e.subject like 'HGNC:%' then e.object else e.subject end as model_gene,
                 e.primary_knowledge_source as source,
                 array_to_string(e.has_evidence, '|') as evidence
          from kg.edges e
          where e.predicate = 'biolink:orthologous_to'
            and (e.subject like 'HGNC:%' or e.object like 'HGNC:%')
        )
        select distinct p.human_gene, p.model_gene, p.source, p.evidence
        from pairs p join kg.nodes m on m.id = p.model_gene
        where m.in_taxon in ({_TAXA_SQL})
        order by 1, 2, 3
    """,
    "genes": f"""
        select id, symbol, name, in_taxon, in_taxon_label
        from kg.nodes
        where category = 'biolink:Gene'
          and (id like 'HGNC:%' or in_taxon in ({_TAXA_SQL}))
        order by 1
    """,
    "disease_closure": """
        select distinct subject_id as child, object_id as ancestor
        from kg.closure
        where predicate_id = 'rdfs:subClassOf'
          and subject_id like 'MONDO:%' and object_id like 'MONDO:%'
          and subject_id <> object_id
        order by 1, 2
    """,
    # MONDO's harrisons_view subset: a top-level, body-system cross-section.
    "disease_areas": """
        select id, name from kg.nodes
        where id like 'MONDO:%' and list_contains(subsets, 'harrisons_view')
        order by 1
    """,
    "diseases": """
        select id, name from kg.nodes
        where id like 'MONDO:%'
        order by 1
    """,
}


def _release_version(location: str) -> str:
    """Read the release version that sits next to a published KG file."""
    explicit = os.environ.get("KG_VERSION")
    if explicit:
        return explicit
    if not location.startswith(("https://", "http://")):
        return "unknown"
    metadata_url = location.rsplit("/", 1)[0] + "/metadata.yaml"
    try:
        with urllib.request.urlopen(metadata_url, timeout=30) as response:  # noqa: S310
            for line in response.read().decode("utf-8").splitlines():
                if line.startswith("version:"):
                    return line.split(":", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return "unknown"


def slice_kg(location: str, output: Path) -> dict[str, Any]:
    """Write the KG slice and its provenance manifest to `output`."""
    import duckdb  # Only the slice step needs DuckDB.

    output = output.expanduser().resolve()
    replace_directory(output)
    connection = duckdb.connect()
    if location.startswith(("https://", "http://")):
        connection.execute("install httpfs; load httpfs;")
    else:
        location = str(Path(location).expanduser().resolve())
        if not Path(location).is_file():
            raise FileNotFoundError(f"KG DuckDB file not found: {location}")
    connection.execute(f"attach '{location}' as kg (read_only)")

    files: list[dict[str, Any]] = []
    for name, query in SLICE_QUERIES.items():
        path = output / f"{name}.tsv.gz"
        connection.execute(f"copy ({query}) to '{path}' (header, delimiter '\t', compression gzip)")
        rows = connection.execute(
            f"select count(*) from read_csv('{path}', delim='\t', header=true, all_varchar=true)"
        ).fetchone()[0]
        files.append({"path": path.name, "rows": rows, "sha256": sha256(path)})

    manifest = {
        "schema_version": 1,
        "extracted_at": now(),
        "location": location,
        "version": _release_version(location),
        "files": files,
    }
    write_json(output / "provenance.json", manifest)
    return manifest


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def verify_slice(source: Path) -> dict[str, Any]:
    from .common import load_json

    manifest_path = source / "provenance.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"KG slice provenance not found: {manifest_path}")
    manifest = load_json(manifest_path)
    expected = set(SLICE_QUERIES)
    listed = {item["path"].removesuffix(".tsv.gz") for item in manifest.get("files") or []}
    if listed != expected:
        raise ValueError(
            f"KG slice inventory disagrees with this pipeline: {sorted(listed ^ expected)}"
        )
    for item in manifest["files"]:
        if sha256(source / item["path"]) != item["sha256"]:
            raise ValueError(f"KG slice file was modified after extraction: {item['path']}")
    return manifest


class KGSlice:
    """In-memory indexes over the extracted slice."""

    def __init__(self, source: Path):
        self.source = source
        self.provenance = verify_slice(source)
        load = lambda name: _read_tsv(source / f"{name}.tsv.gz")  # noqa: E731

        self.model_of = [row for row in load("model_of") if row["negated"].lower() != "true"]
        self.genotypes = {row["id"]: row for row in load("genotypes")}
        self.genes = {row["id"]: row for row in load("genes")}
        self.disease_labels = {row["id"]: row["name"] for row in load("diseases")}
        self.disease_areas = {row["id"]: row["name"] for row in load("disease_areas")}

        self.variants_by_genotype: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in load("genotype_variants"):
            self.variants_by_genotype[row["genotype"]].append(row)
        self.genes_by_variant: dict[str, list[str]] = defaultdict(list)
        for row in load("variant_genes"):
            self.genes_by_variant[row["variant"]].append(row["gene"])
        self.direct_genes_by_genotype: dict[str, list[str]] = defaultdict(list)
        for row in load("genotype_genes"):
            self.direct_genes_by_genotype[row["genotype"]].append(row["gene"])

        self.orthologs_by_model: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.orthologs_by_human: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in load("orthologs"):
            self.orthologs_by_model[row["model_gene"]].append(row)
            self.orthologs_by_human[row["human_gene"]].append(row)

        self.ancestors: dict[str, set[str]] = defaultdict(set)
        self.descendants: dict[str, set[str]] = defaultdict(set)
        for row in load("disease_closure"):
            self.ancestors[row["child"]].add(row["ancestor"])
            self.descendants[row["ancestor"]].add(row["child"])
        self.descendant_counts = {
            disease: len(children) for disease, children in self.descendants.items()
        }

    def gene(self, identifier: str) -> dict[str, Any]:
        node = self.genes.get(identifier) or {}
        taxon = node.get("in_taxon")
        return {
            "id": identifier,
            "symbol": node.get("symbol") or node.get("name") or identifier,
            "taxon": {"id": taxon, "label": node.get("in_taxon_label")} if taxon else None,
            "url": expand_curie(identifier, _GENE_PREFIXES),
        }

    def correspondences(
        self, *, model_gene: str | None = None, human_gene: str | None = None
    ) -> list[dict[str, Any]]:
        """Derived orthology correspondences touching a model gene or a human gene."""
        rows = (
            self.orthologs_by_model.get(model_gene, [])
            if model_gene
            else self.orthologs_by_human.get(human_gene or "", [])
        )
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[(row["human_gene"], row["model_gene"])].append(
                {
                    "source": row["source"],
                    "evidence": [value for value in row["evidence"].split("|") if value],
                }
            )
        return [
            {
                "human_gene": human,
                "model_gene": model,
                "correspondence": {
                    "relation": "ORTHOLOGOUS",
                    "basis": "DERIVED",
                    "support": sorted(support, key=lambda item: item["source"]),
                },
            }
            for (human, model), support in sorted(grouped.items())
        ]


_GENE_PREFIXES = {
    "HGNC": "https://www.genenames.org/data/gene-symbol-report/#!/hgnc_id/HGNC:",
    "MGI": "https://www.informatics.jax.org/marker/MGI:",
    "ZFIN": "https://zfin.org/",
    "RGD": "https://rgd.mcw.edu/rgdweb/report/gene/main.html?id=",
    "NCBIGene": "https://www.ncbi.nlm.nih.gov/gene/",
    "FB": "https://flybase.org/reports/",
    "WB": "https://wormbase.org/species/c_elegans/gene/",
    "Xenbase": "https://www.xenbase.org/entry/",
}


KG_COMPONENT_SOURCE = "Monarch KG"
ZFIN_COMPONENT_SOURCE = "ZFIN fish components"


def _genotype_components(kg: KGSlice, genotype_id: str, zfin) -> list[dict[str, Any]]:
    """Gene-level components from the KG, completed from ZFIN fish components.

    The KG supplies genotype -> allele -> gene. For ZFIN fish, the ZFIN download
    adds reagent-targeted genes and allele -> gene links the KG lacks; each
    component records which source supplied its gene.
    """
    genes: dict[str | None, dict[str, Any]] = {}

    def entry(gene_id, source):
        return genes.setdefault(
            gene_id, {"alleles": [], "reagents": [], "source": source, "symbol": None}
        )

    for variant in kg.variants_by_genotype.get(genotype_id, []):
        allele_label, _ = plain_genotype_label(variant["variant_label"] or variant["variant"])
        allele = {"id": variant["variant"], "label": allele_label}
        for gene_id in kg.genes_by_variant.get(variant["variant"]) or [None]:
            item = entry(gene_id, KG_COMPONENT_SOURCE)
            if allele not in item["alleles"]:
                item["alleles"].append(allele)
    for gene_id in kg.direct_genes_by_genotype.get(genotype_id, []):
        entry(gene_id, KG_COMPONENT_SOURCE)

    if zfin is not None and genotype_id.startswith("ZFIN:"):
        placed: set[str] = set()
        for gene_id, found in zfin.genes(genotype_id).items():
            item = entry(gene_id, ZFIN_COMPONENT_SOURCE)
            item["symbol"] = item["symbol"] or found["symbol"]
            for reagent in found["reagents"]:
                if reagent not in item["reagents"]:
                    item["reagents"].append(reagent)
            known = {allele["id"] for allele in item["alleles"]}
            item["alleles"].extend(a for a in found["alleles"] if a["id"] not in known)
            placed.update(allele["id"] for allele in found["alleles"])
        # Alleles the KG left without a gene, which ZFIN has now placed.
        if None in genes:
            genes[None]["alleles"] = [a for a in genes[None]["alleles"] if a["id"] not in placed]
            if not genes[None]["alleles"]:
                del genes[None]

    components: list[dict[str, Any]] = []
    for gene_id, item in sorted(genes.items(), key=lambda pair: str(pair[0])):
        base = {
            "alleles": item["alleles"],
            "reagents": item["reagents"],
            "source": item["source"],
            "human_gene": None,
            "human_gene_basis": "NOT_REPORTED",
            "correspondence": None,
        }
        if gene_id is None:
            components.append({**base, "model_gene": None, "model_gene_basis": "NOT_REPORTED"})
            continue
        model_gene = kg.gene(gene_id)
        if item["symbol"] and model_gene["symbol"] == gene_id:
            model_gene["symbol"] = item["symbol"]
        derived = kg.correspondences(model_gene=gene_id)
        if not derived:
            components.append({**base, "model_gene": model_gene, "model_gene_basis": "CURATED"})
        for match in derived:
            components.append(
                {
                    **base,
                    "model_gene": model_gene,
                    "model_gene_basis": "CURATED",
                    "human_gene": kg.gene(match["human_gene"]),
                    "human_gene_basis": "DERIVED",
                    "correspondence": match["correspondence"],
                }
            )
    return components


def kg_model_records(kg: KGSlice, zfin=None) -> list[dict[str, Any]]:
    """One record per genotype, carrying every `model_of` association it has."""
    associations: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in kg.model_of:
        associations[row["genotype"]].append(row)

    records: list[dict[str, Any]] = []
    for genotype_id, rows in sorted(associations.items()):
        node = kg.genotypes.get(genotype_id) or {}
        label, background = plain_genotype_label(node.get("name") or genotype_id)
        source = MODEL_SOURCES[rows[0]["source"]]
        taxon = node.get("in_taxon")

        components = _genotype_components(kg, genotype_id, zfin)

        disease_associations = []
        publications: list[str] = []
        for row in rows:
            row_publications = [value for value in row["publications"].split("|") if value]
            publications.extend(row_publications)
            original = row.get("original_disease") or None
            disease_associations.append(
                {
                    "disease": {
                        "id": row["disease"],
                        "label": kg.disease_labels.get(row["disease"]) or row["disease"],
                        "url": expand_curie(
                            row["disease"], {"MONDO": "http://purl.obolibrary.org/obo/MONDO_"}
                        ),
                    },
                    "original_disease": (
                        {"id": original, "label": None}
                        if original and original != row["disease"]
                        else None
                    ),
                    "source": row["source"],
                    "publications": row_publications,
                    "evidence_codes": [
                        value for value in row["evidence_codes"].split("|") if value
                    ],
                }
            )

        records.append(
            {
                "id": genotype_id,
                "name": label,
                "description": None,
                "source": source,
                "species": {"id": taxon, "label": node.get("in_taxon_label")} if taxon else None,
                "species_as_curated": node.get("in_taxon_label"),
                "species_basis": "CURATED" if taxon else "NOT_REPORTED",
                "strain_background": background,
                "model_category": None,
                "model_category_as_curated": None,
                "model_category_basis": "NOT_REPORTED",
                "genotype": {"id": genotype_id, "label": label},
                "genetic_components": components,
                "disease_associations": disease_associations,
                "modeled_mechanisms": [],
                "publications": strings(publications),
                "notes": None,
                "evidence": [],
                "source_record_url": f"{MONARCH_SITE}/{genotype_id}",
                "source_yaml_url": None,
                "context_id": None,
                "context_kind": None,
                "context_name": None,
            }
        )
    return records
