"""Build the catalog from verified DisMech and KG snapshots, and validate the output."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .common import (
    NAMO_DOCS,
    NAMO_REPOSITORY,
    git_metadata,
    git_root,
    load_json,
    load_yaml,
    prefix_map,
    replace_directory,
    sha256,
    strings,
    write_json,
)
from .dismech_source import SCHEMA_PATH, dismech_records, verify_snapshot
from .kg_source import KGSlice, kg_model_records
from .matching import (
    CLOSE_PARENT_MAX_DESCENDANTS,
    PRECISION_LABELS,
    annotate_kg_precision,
    coverage_rows,
    find_counterparts,
)
from .normalize import CATEGORY_LABELS
from .zfin_source import ZfinComponents, release_date

SOURCE_LABELS = {"DISMECH": "DisMech", "MGI": "MGI", "ZFIN": "ZFIN", "RGD": "RGD"}
SOURCE_GROUPS = {
    "DISMECH": "DisMech curation",
    "MGI": "Alliance member (Monarch KG)",
    "ZFIN": "Alliance member (Monarch KG)",
    "RGD": "Alliance member (Monarch KG)",
}
BASIS_LABELS = {
    "CURATED": "Curated",
    "SAFE_MAPPING": "Safe mapping",
    "DERIVED": "Derived (KG orthology)",
    "NOT_REPORTED": "Not reported",
    "NOT_NORMALIZED": "Not normalized",
}
NAMO_CLASS = "AnimalModel"
PERTURBATION_LABELS = {"MORPHOLINO": "Morpholino", "CRISPR": "CRISPR", "TALEN": "TALEN"}


def _source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _namo_profile(namo_schema_path: Path) -> dict[str, Any]:
    schema = load_yaml(namo_schema_path)
    definition = (schema.get("classes") or {}).get(NAMO_CLASS)
    if not isinstance(definition, dict):
        raise ValueError(f"NAMO schema has no {NAMO_CLASS} class: {namo_schema_path}")
    root = git_root(namo_schema_path)
    metadata = (
        git_metadata(root, NAMO_REPOSITORY)
        if root
        else {
            "revision": "unknown",
            "branch": "unknown",
            "dirty": False,
            "repository": NAMO_REPOSITORY,
        }
    )
    metadata["schema_sha256"] = sha256(namo_schema_path)
    return {
        "curie": f"namo:{NAMO_CLASS}",
        "docs_url": f"{NAMO_DOCS}/{quote(NAMO_CLASS)}/",
        "attributes": sorted((definition.get("attributes") or {}).keys()),
        "metadata": metadata,
    }


def _browser_fields(
    model: dict[str, Any], counterpart_ids: list[str], counterpart_bases: list[str]
) -> dict[str, Any]:
    """Flat, facet-friendly fields derived from the normalized record."""
    components = model.get("genetic_components") or []
    human = strings((c.get("human_gene") or {}).get("symbol") for c in components)
    model_genes = strings((c.get("model_gene") or {}).get("symbol") for c in components)
    gene_bases = strings(
        f"Human gene: {BASIS_LABELS[c['human_gene_basis']].lower()}"
        for c in components
        if c.get("human_gene")
    ) + strings(
        f"Model gene: {BASIS_LABELS[c['model_gene_basis']].lower()}"
        for c in components
        if c.get("model_gene")
    )
    associations = model.get("disease_associations") or []
    precision = strings(
        PRECISION_LABELS.get(a.get("dismech_precision"))
        for a in associations
        if a.get("dismech_precision")
    )
    if model["source"] == "DISMECH":
        precision = (
            ["DisMech entry"] if model.get("context_kind") == "Disorder" else ["DisMech module"]
        )
    mechanisms = model.get("modeled_mechanisms") or []
    return {
        "source_label": SOURCE_LABELS[model["source"]],
        "source_group": SOURCE_GROUPS[model["source"]],
        "species_label": (model.get("species") or {}).get("label"),
        "species_basis_label": BASIS_LABELS[model["species_basis"]],
        "model_category_label": CATEGORY_LABELS.get(model.get("model_category") or ""),
        "disease_labels": strings(a["disease"]["label"] for a in associations)
        or ([model["context_name"]] if model.get("context_name") else []),
        "dismech_precision_labels": precision,
        "human_gene_symbols": human,
        "model_gene_symbols": model_genes,
        "gene_basis_labels": gene_bases,
        "allele_labels": strings(
            a.get("label") for c in components for a in c.get("alleles") or []
        ),
        "reagent_labels": strings(
            r.get("label") for c in components for r in c.get("reagents") or []
        ),
        "perturbation_labels": strings(
            [
                *(["Allele"] if any(c.get("alleles") for c in components) else []),
                *(
                    PERTURBATION_LABELS[r["reagent_type"]]
                    for c in components
                    for r in c.get("reagents") or []
                ),
            ]
        ),
        "mechanism_names": strings(m["target"] for m in mechanisms),
        "relationships": strings(m["relationship_label"] for m in mechanisms),
        "fidelities": strings(m["fidelity_label"] for m in mechanisms),
        "mechanism_status": "Linked to pathograph" if mechanisms else "No pathograph links",
        "phenotype_labels": strings(p["label"] for p in model.get("associated_phenotypes") or []),
        "publication_status": "Publication cited"
        if model.get("publications")
        else "No publication",
        "counterpart_ids": counterpart_ids,
        "counterpart_count": len(counterpart_ids),
        "counterpart_status": "Has related models" if counterpart_ids else "No related models",
        "counterpart_basis_labels": counterpart_bases,
        "evidence_text": strings(e.get("snippet") for e in model.get("evidence") or []),
    }


def _annotate_disease_rollups(models: list[dict[str, Any]], kg: KGSlice) -> dict[str, str]:
    """Roll each model's diseases up MONDO for the Disease area and subtype filters.

    Disease areas are MONDO's harrisons_view terms. Disease groups are every
    other ancestor, excluding the area terms and anything more general than
    them ("disease", "human disease"), so picking a group such as "muscular
    dystrophy" matches models of any of its subtypes. Returns group id -> label.
    """
    areas = set(kg.disease_areas)
    too_general = areas | {a for area in areas for a in kg.ancestors.get(area, ())}
    labels: dict[str, str] = {}
    for model in models:
        lineage: set[str] = set()
        for association in model.get("disease_associations") or []:
            disease = association["disease"]["id"]
            lineage |= {disease} | kg.ancestors.get(disease, set())
        model["disease_area_labels"] = sorted(kg.disease_areas[a] for a in lineage & areas)
        groups = sorted(lineage - too_general)
        model["disease_group_ids"] = groups
        for group in groups:
            labels[group] = kg.disease_labels.get(group) or group
    return dict(sorted(labels.items()))


def _other_side(pair: dict[str, Any], model_id: str) -> str:
    return pair["kg_model_id"] if pair["dismech_model_id"] == model_id else pair["dismech_model_id"]


def build_site(
    dismech_source: Path, kg_source: Path, zfin_source: Path, namo_schema_path: Path, output: Path
) -> dict[str, Any]:
    dismech_source = dismech_source.expanduser().resolve()
    kg_source = kg_source.expanduser().resolve()
    zfin_source = zfin_source.expanduser().resolve()
    output = output.expanduser()
    if output.is_symlink():
        raise ValueError(f"Refusing to replace a symlinked output directory: {output}")
    output = output.resolve()

    dismech_provenance = verify_snapshot(dismech_source)
    kg = KGSlice(kg_source)
    zfin = ZfinComponents(zfin_source)
    namo = _namo_profile(namo_schema_path.expanduser().resolve())
    prefixes = prefix_map(load_yaml(dismech_source / SCHEMA_PATH))

    dismech_models, contexts, warnings = dismech_records(
        dismech_source, dismech_provenance, prefixes, kg
    )
    kg_models = kg_model_records(kg, zfin)
    contexts_by_mondo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for context in contexts:
        if context["kind"] == "Disorder" and context.get("mondo"):
            contexts_by_mondo[context["mondo"]].append(context)
    annotate_kg_precision(kg_models, contexts_by_mondo, kg)
    counterparts = find_counterparts(dismech_models, kg_models, kg)
    diseases = coverage_rows(contexts, dismech_models, kg_models, kg)

    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pair in counterparts:
        by_model[pair["dismech_model_id"]].append(pair)
        by_model[pair["kg_model_id"]].append(pair)
    models = [*dismech_models, *kg_models]
    for model in models:
        pairs = by_model.get(model["id"], [])
        model["namo_type"] = namo["curie"]
        model.update(
            _browser_fields(
                model,
                [p["id"] for p in pairs],
                strings(label for p in pairs for label in p["basis_labels"]),
            )
        )
        # The record plus every related record, so "related to X" is one filter value.
        others = [_other_side(p, model["id"]) for p in pairs]
        model["related_ids"] = [model["id"], *others]
    disease_groups = _annotate_disease_rollups(models, kg)
    models.sort(key=lambda m: (m["name"].casefold(), m["id"]))

    for context in contexts:
        if context["kind"] == "Disorder" and not context.get("mondo"):
            warnings.append(f"DisMech entry has no MONDO disease term: {context['source_path']}")

    source_counts = Counter(m["source"] for m in models)
    stats = {
        "model_count": len(models),
        "dismech_model_count": source_counts["DISMECH"],
        "kg_model_count": len(kg_models),
        "kg_model_counts_by_source": {k: source_counts[k] for k in ("MGI", "ZFIN", "RGD")},
        "disease_count": len(diseases),
        "coverage_counts": dict(sorted(Counter(r["coverage"] for r in diseases).items())),
        "counterpart_count": len(counterparts),
        "mechanism_link_count": sum(len(m["modeled_mechanisms"]) for m in dismech_models),
        "close_parent_max_descendants": CLOSE_PARENT_MAX_DESCENDANTS,
        "species_count": len({m["species"]["id"] for m in models if m.get("species")}),
        "modeled_disease_count": len(
            {a["disease"]["id"] for m in models for a in m.get("disease_associations") or []}
        ),
    }
    model_schema = load_json(_source_root() / "config/models.browser.json")
    catalog = {
        "schema_version": 1,
        "generated_at": dismech_provenance["copied_at"],
        "stats": stats,
        "provenance": {
            "dismech": dismech_provenance["dismech"],
            "dismech_copy": {
                "copied_at": dismech_provenance["copied_at"],
                "file_count": dismech_provenance["file_count"],
                "source_fingerprint": dismech_provenance.get("source_fingerprint"),
            },
            "kg": {k: kg.provenance[k] for k in ("location", "version", "extracted_at")},
            "zfin": {
                **{k: zfin.provenance.get(k) for k in ("url", "last_modified", "retrieved_at")},
                "version": zfin.provenance.get("version")
                or release_date(
                    zfin.provenance.get("last_modified"), zfin.provenance["retrieved_at"]
                ),
            },
            "namo": {**namo["metadata"], "class": namo["curie"], "docs_url": namo["docs_url"]},
            "warnings": warnings,
        },
        "schemas": {"models": model_schema},
        "models": models,
        "diseases": diseases,
        "counterparts": counterparts,
        "disease_groups": disease_groups,
    }

    site_source = _source_root() / "src/site"
    for other in (dismech_source, kg_source, zfin_source, site_source):
        if output == other or output in other.parents or other in output.parents:
            raise ValueError(f"Build output must not overlap {other}")
    replace_directory(output)
    shutil.copytree(site_source, output, dirs_exist_ok=True)
    data = output / "data"
    write_json(data / "catalog.json", catalog, compact=True)
    write_json(data / "provenance.json", catalog["provenance"])
    _write_browser_data(catalog, data)
    return catalog


DETAIL_SHARDS = 64
# Fields every slim index record keeps in addition to those its browser schema names.
INDEX_EXTRAS = [
    "id",
    "name",
    "source",
    "description",
    "context_name",
    "strain_background",
    "counterpart_ids",
    "counterpart_count",
    "related_ids",
    "model_category_label",
    "allele_labels",
    "source_record_url",
]


def detail_shard(record_id: str) -> str:
    digest = hashlib.sha1(record_id.encode("utf-8")).hexdigest()  # noqa: S324 - not security
    return f"m{int(digest[:8], 16) % DETAIL_SHARDS:02d}"


def _index_fields(schema: dict[str, Any]) -> list[str]:
    fields = list(INDEX_EXTRAS)
    fields += schema.get("searchableFields") or []
    fields += [
        item["field"]
        for section in ("facets", "displayFields")
        for item in schema.get(section) or []
    ]
    return list(dict.fromkeys(fields))


def _slim(record: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    slim = {field: record.get(field) for field in fields if field in record}
    if isinstance(slim.get("description"), str) and len(slim["description"]) > 320:
        slim["description"] = slim["description"][:317].rstrip() + "…"
    if isinstance(slim.get("evidence_text"), list):
        slim["evidence_text"] = [text[:200] for text in slim["evidence_text"][:3]]
    return slim


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _write_browser_data(catalog: dict[str, Any], data: Path) -> None:
    """Slim index for search and facets; full records in shards loaded on demand."""
    index = {
        key: catalog[key]
        for key in ("schema_version", "generated_at", "stats", "provenance", "schemas")
    }
    index["counterparts"] = catalog["counterparts"]
    index["disease_groups"] = catalog["disease_groups"]
    # DisMech entry names and pages, for linking KG disease associations to DisMech.
    index["dismech_entries"] = {
        row["dismech_entry"]: {"name": row["name"], "page_url": row["dismech_page_url"]}
        for row in catalog["diseases"]
        if row["dismech_entry"]
    }
    shards: dict[str, dict[str, Any]] = defaultdict(dict)
    fields = _index_fields(catalog["schemas"]["models"])
    index["models"] = []
    for record in catalog["models"]:
        shard = detail_shard(record["id"])
        index["models"].append({**_slim(record, fields), "detail_shard": shard})
        shards[shard][record["id"]] = record
    (data / "index.js").write_text(
        f"window.ANIMAL_MODELS_INDEX={_compact(index)};\n", encoding="utf-8"
    )
    details = data / "details"
    details.mkdir(parents=True, exist_ok=True)
    for shard, records in sorted(shards.items()):
        (details / f"{shard}.js").write_text(
            f"window.ANIMAL_MODELS_DETAIL({_compact(shard)},{_compact(records)});\n",
            encoding="utf-8",
        )


# --- validation ---

MODEL_SOURCES = set(SOURCE_LABELS)
COMPONENT_SOURCES = {"DisMech", "Monarch KG", "ZFIN fish components"}
VALUE_BASES = set(BASIS_LABELS)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_browser_schema(
    view: str, schema: dict[str, Any], records: list[dict[str, Any]]
) -> None:
    available = set().union(*(r.keys() for r in records))
    fields = set(schema.get("searchableFields") or []) | {
        str(item["field"])
        for section in ("facets", "displayFields")
        for item in schema.get(section) or []
    }
    _check(
        not fields - available,
        f"{view} browser schema references unknown fields: {sorted(fields - available)}",
    )
    for facet in schema.get("facets") or []:
        expected = {"string": str, "array": list}[facet["type"]]
        for record in records:
            value = record.get(facet["field"])
            _check(
                value is None or isinstance(value, expected),
                f"{view} facet {facet['field']!r} expects {facet['type']}",
            )


def validate_site(site: Path) -> dict[str, Any]:
    """Fail closed on malformed or internally inconsistent output."""
    site = site.expanduser().resolve()
    for path in (
        "index.html",
        "assets/app.js",
        "assets/core.js",
        "assets/styles.css",
        "data/catalog.json",
        "data/index.js",
    ):
        _check((site / path).is_file(), f"Generated site is missing {path}")
    catalog = load_json(site / "data/catalog.json")
    models, diseases, counterparts = catalog["models"], catalog["diseases"], catalog["counterparts"]
    _check(bool(models) and bool(diseases), "Catalog has no models or no disease rows")

    for name, records in (
        ("models", models),
        ("diseases", diseases),
        ("counterparts", counterparts),
    ):
        ids = [r.get("id") for r in records]
        _check(
            all(isinstance(i, str) and i for i in ids) and len(ids) == len(set(ids)),
            f"{name} contain missing or duplicate ids",
        )
    by_id = {m["id"]: m for m in models}
    pair_by_id = {p["id"]: p for p in counterparts}
    disease_groups = catalog.get("disease_groups") or {}

    for model in models:
        mid = model["id"]
        _check(model["source"] in MODEL_SOURCES, f"Invalid source: {mid}")
        _check(model["species_basis"] in VALUE_BASES, f"Invalid species basis: {mid}")
        _check(
            (model["species_basis"] in {"CURATED", "SAFE_MAPPING"}) == bool(model.get("species")),
            f"Species and its basis disagree: {mid}",
        )
        _check(
            str(model.get("source_record_url") or "").startswith("https://"),
            f"Model lacks a source URL: {mid}",
        )
        if model["source"] == "DISMECH":
            _check(mid.startswith("dismech:kb/"), f"DisMech id has the wrong shape: {mid}")
            _check(
                str(model.get("source_yaml_url") or "").startswith("https://"),
                f"DisMech model lacks a YAML permalink: {mid}",
            )
        else:
            _check(not model["modeled_mechanisms"], f"KG model has DisMech mechanisms: {mid}")
            _check(bool(model["disease_associations"]), f"KG model has no disease: {mid}")
            for association in model["disease_associations"]:
                _check(
                    association.get("dismech_precision") in PRECISION_LABELS,
                    f"KG association lacks a DisMech precision: {mid}",
                )
        for component in model.get("genetic_components") or []:
            for side in ("human_gene", "model_gene"):
                basis = component.get(f"{side}_basis")
                _check(basis in VALUE_BASES, f"Invalid {side} basis: {mid}")
                _check(
                    (basis != "NOT_REPORTED") == bool(component.get(side)),
                    f"{side} and its basis disagree: {mid}",
                )
            _check(
                all(
                    r.get("reagent_type") in PERTURBATION_LABELS
                    for r in component.get("reagents") or []
                ),
                f"Invalid reagent type: {mid}",
            )
            _check(component.get("source") in COMPONENT_SOURCES, f"Invalid component source: {mid}")
            correspondence = component.get("correspondence")
            if correspondence:
                _check(
                    bool(component.get("human_gene")) and bool(component.get("model_gene")),
                    f"Correspondence without both genes: {mid}",
                )
                _check(
                    correspondence["basis"] in VALUE_BASES, f"Invalid correspondence basis: {mid}"
                )
                _check(
                    correspondence["basis"] != "DERIVED" or bool(correspondence.get("support")),
                    f"Derived correspondence without support: {mid}",
                )
        for mechanism in model["modeled_mechanisms"]:
            hood = mechanism["neighborhood"]
            node_ids = {n["id"] for n in hood["nodes"]}
            _check(
                hood["model_node_id"] == mid and hood["focus_node_id"] in node_ids,
                f"Neighborhood has invalid model or focus node: {mid}",
            )
            _check(
                all(
                    e["source_id"] in node_ids and e["target_id"] in node_ids for e in hood["edges"]
                ),
                f"Neighborhood has an orphan edge: {mid}",
            )
        others = set()
        for pair_id in model["counterpart_ids"]:
            pair = pair_by_id.get(pair_id)
            _check(pair is not None, f"Model references an unknown related model: {mid}")
            others.add(_other_side(pair, mid))
        _check(
            model["related_ids"][:1] == [mid] and set(model["related_ids"][1:]) == others,
            f"Related ids disagree with related-model pairs: {mid}",
        )
        _check(
            all(group in disease_groups for group in model["disease_group_ids"]),
            f"Model references an unlabeled disease group: {mid}",
        )
        _check(
            model["counterpart_count"] == len(model["counterpart_ids"]),
            f"Counterpart count disagrees: {mid}",
        )

    for pair in counterparts:
        dismech, other = by_id.get(pair["dismech_model_id"]), by_id.get(pair["kg_model_id"])
        _check(
            bool(dismech) and dismech["source"] == "DISMECH",
            f"Counterpart lacks DisMech side: {pair['id']}",
        )
        _check(
            bool(other) and other["source"] != "DISMECH", f"Counterpart lacks KG side: {pair['id']}"
        )
        _check(
            pair["id"] in dismech["counterpart_ids"] and pair["id"] in other["counterpart_ids"],
            f"Counterpart is not linked from both records: {pair['id']}",
        )
        _check(bool(pair["bases"]), f"Counterpart has no basis: {pair['id']}")
        _check(
            ("SAME_PUBLICATION" in pair["bases"]) == bool(pair["shared_publications"]),
            f"Shared-publication basis disagrees: {pair['id']}",
        )
        _check(
            set(pair["shared_publications"])
            <= set(dismech["publications"]) & set(other["publications"]),
            f"Counterpart claims a publication one side lacks: {pair['id']}",
        )

    for row in diseases:
        for field in (
            "dismech_model_ids",
            "kg_exact_model_ids",
            "kg_subtype_model_ids",
            "kg_close_parent_model_ids",
        ):
            _check(
                all(i in by_id for i in row[field]),
                f"Disease row references unknown model: {row['id']}",
            )
            _check(
                row[field.replace("_ids", "_count")] == len(row[field]),
                f"Disease row count disagrees: {row['id']}",
            )
        for model_id in row["kg_exact_model_ids"]:
            _check(
                row["disease"]["id"]
                in {a["disease"]["id"] for a in by_id[model_id]["disease_associations"]},
                f"Exact KG model does not assert this disease: {row['id']}",
            )
        _check(
            (row["dismech_entry"] is None) == (row["dismech_entry_status"] == "No DisMech entry"),
            f"Disease row entry status disagrees: {row['id']}",
        )

    _validate_browser_schema("models", catalog["schemas"]["models"], models)

    stats = catalog["stats"]
    _check(stats["model_count"] == len(models), "model_count disagrees")
    _check(stats["disease_count"] == len(diseases), "disease_count disagrees")
    _check(stats["counterpart_count"] == len(counterparts), "counterpart_count disagrees")
    _check(
        stats["dismech_model_count"] + stats["kg_model_count"] == len(models),
        "Per-source model counts disagree",
    )
    return {"model_count": len(models), "disease_count": len(diseases)}
