"""Copy a DisMech snapshot with provenance and extract its animal models."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .common import (
    DISMECH_REPOSITORY,
    DISMECH_SITE,
    anchor,
    as_list,
    canonical_curie,
    expand_curie,
    git_metadata,
    humanize,
    load_json,
    load_yaml,
    merge_evidence,
    normalize_evidence,
    now,
    replace_directory,
    sha256,
    slugify_page,
    strings,
    term,
    write_json,
)
from .normalize import normalize_category, normalize_species

SCHEMA_PATH = "src/dismech/schema/dismech.yaml"

PATHOGRAPH_NODE_SECTIONS = (
    ("pathophysiology", "pathophysiology", "Pathophysiology event"),
    ("phenotypes", "phenotype", "Phenotype"),
    ("environmental", "environmental", "Environmental factor"),
    ("genetic", "genetic", "Genetic factor"),
    ("treatments", "treatment", "Treatment"),
    ("biochemical", "biochemical", "Biochemical finding"),
)

CAUSAL_EDGE_LABELS = {
    "DIRECT": "Directly causes",
    "INDIRECT_KNOWN_INTERMEDIATES": "Indirectly causes (known intermediates)",
    "INDIRECT_UNKNOWN_INTERMEDIATES": "Indirectly causes (unknown intermediates)",
    "UNKNOWN": "Causes (directness unknown)",
}


def sync_dismech(dismech_root: Path, output: Path) -> dict[str, Any]:
    """Copy only the DisMech files this build reads, with a checksum inventory."""
    dismech_root = dismech_root.expanduser().resolve()
    schema_path = dismech_root / SCHEMA_PATH
    if not schema_path.is_file():
        raise FileNotFoundError(f"DisMech schema not found: {schema_path}")
    disorders = dismech_root / "kb/disorders"
    if not disorders.is_dir():
        raise FileNotFoundError(f"DisMech disorder directory not found: {disorders}")

    output = output.expanduser()
    if output.is_symlink():
        raise ValueError(f"Refusing to replace a symlinked output directory: {output}")
    output = output.resolve()
    if output == dismech_root or output in dismech_root.parents or dismech_root in output.parents:
        raise ValueError("Sync output must not overlap the DisMech source repository")
    replace_directory(output)

    targets = [(schema_path, Path(SCHEMA_PATH))]
    for source_dir in (disorders, dismech_root / "kb/modules"):
        if source_dir.is_dir():
            targets.extend(
                (path, path.relative_to(dismech_root))
                for path in sorted(source_dir.glob("*.yaml"))
                if not path.name.endswith(".history.yaml")
            )
    copied: list[dict[str, str]] = []
    for source, relative in targets:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append({"path": relative.as_posix(), "sha256": sha256(destination)})

    manifest = {
        "schema_version": 1,
        "copied_at": now(),
        "source_root": str(dismech_root),
        "dismech": git_metadata(dismech_root, DISMECH_REPOSITORY),
        "file_count": len(copied),
        "files": copied,
    }
    manifest["source_fingerprint"] = hashlib.sha256(
        json.dumps(copied, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    write_json(output / "provenance.json", manifest)
    return manifest


def entry_files(source: Path) -> list[tuple[str, Path]]:
    result = [
        ("Disorder", path)
        for path in sorted((source / "kb/disorders").glob("*.yaml"))
        if not path.name.endswith(".history.yaml")
    ]
    modules = source / "kb/modules"
    if modules.is_dir():
        result.extend(
            ("Module", path)
            for path in sorted(modules.glob("*.yaml"))
            if not path.name.endswith(".history.yaml")
        )
    return result


def verify_snapshot(source: Path) -> dict[str, Any]:
    """Fail if copied inputs no longer match their provenance manifest."""
    provenance_path = source / "provenance.json"
    if not provenance_path.is_file():
        raise FileNotFoundError(f"Source provenance manifest not found: {provenance_path}")
    provenance = load_json(provenance_path)
    inventoried: set[str] = set()
    for item in provenance.get("files") or []:
        relative = Path(str(item["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe path in source provenance manifest: {relative}")
        path = source / relative
        if not path.is_file():
            raise ValueError(f"Inventoried source file is missing: {relative}")
        if sha256(path) != item["sha256"]:
            raise ValueError(f"Inventoried source file was modified after sync: {relative}")
        inventoried.add(relative.as_posix())
    consumed = {SCHEMA_PATH} | {
        path.relative_to(source).as_posix() for _, path in entry_files(source)
    }
    if consumed - inventoried:
        raise ValueError(
            f"Source snapshot has uninventoried inputs: {sorted(consumed - inventoried)}"
        )
    return provenance


def _has_animal_models(path: Path) -> bool:
    with path.open(encoding="utf-8") as stream:
        return any(line.startswith("animal_models:") for line in stream)


def _disease_mondo(data: dict[str, Any]) -> str | None:
    disease_term = data.get("disease_term")
    if isinstance(disease_term, dict):
        value = (
            (disease_term.get("term") or {}).get("id")
            if isinstance(disease_term.get("term"), dict)
            else disease_term.get("id")
        )
        value = canonical_curie(str(value)) if value else None
        if value and value.startswith("MONDO:"):
            return value
    return None


def context_metadata(
    data: dict[str, Any], source_kind: str, relative: Path, provenance: dict[str, Any]
) -> dict[str, Any]:
    name = str(data.get("name") or relative.stem)
    page_path = (
        f"pages/disorders/{slugify_page(name)}.html"
        if source_kind == "Disorder"
        else f"pages/modules/{relative.stem}.html"
    )
    revision = provenance["dismech"]["revision"]
    repository = provenance["dismech"].get("repository") or DISMECH_REPOSITORY
    # Some entries store `name` as an underscore slug; show it with spaces but
    # keep the stored value for the page path, which DisMech derives from it.
    display_name = name.replace("_", " ") if "_" in name and " " not in name else name
    return {
        "id": f"{source_kind.casefold()}:{relative.stem}",
        "name": display_name,
        "kind": source_kind,
        "source_path": relative.as_posix(),
        "source_yaml_url": f"{repository}/blob/{revision}/{relative.as_posix()}",
        "page_url": f"{DISMECH_SITE}/{page_path}",
        "mondo": _disease_mondo(data) if source_kind == "Disorder" else None,
        "synonyms": strings(data.get("synonyms") or []),
    }


def animal_model_label(model: dict[str, Any]) -> str | None:
    """DisMech's own display label: `name`, else genotype and species (graph.py)."""
    name = str(model.get("name") or "").strip()
    if name:
        return name
    parts = [
        str(model.get(key)).strip()
        for key in ("genotype", "species")
        if str(model.get(key) or "").strip()
    ]
    return " ".join(parts) or None


# --- pathograph neighborhoods (same contract as monarch-nams, version 1) ---


def _node_id(context: dict[str, Any], kind: str, name: str) -> str:
    return f"node:{quote(str(context['id']), safe='')}:{kind}:{quote(name, safe='')}"


def _node_url(context: dict[str, Any], kind: str, name: str) -> str:
    prefix = (
        "module-pathophysiology"
        if context["kind"] == "Module" and kind == "pathophysiology"
        else kind.replace("_", "-")
    )
    return f"{context['page_url']}#{anchor(prefix, name)}"


def _node(
    context,
    kind,
    kind_label,
    name,
    item=None,
    *,
    distance=None,
    resolved=True,
    status="resolved",
    candidate_kinds=None,
    url=True,
) -> dict[str, Any]:
    item = item or {}
    return {
        "id": _node_id(context, kind, name),
        "kind": kind,
        "kind_label": kind_label,
        "label": name,
        "description": item.get("description"),
        "url": _node_url(context, kind, name) if url else None,
        "resolved": resolved,
        "resolution_status": status,
        "candidate_kinds": candidate_kinds if candidate_kinds is not None else [kind],
        "distance": distance,
        "biological_scale": item.get("biological_scale"),
        "biological_scale_label": humanize(item.get("biological_scale")),
        "mechanism_confidence": item.get("mechanism_confidence"),
        "mechanism_confidence_label": humanize(item.get("mechanism_confidence")),
    }


def pathograph_index(entry: dict[str, Any], context: dict[str, Any]):
    nodes_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pathophysiology: dict[str, dict[str, Any]] = {}
    for section, kind, kind_label in PATHOGRAPH_NODE_SECTIONS:
        for index, item in enumerate(as_list(entry.get(section))):
            if not isinstance(item, dict) or not item.get("name"):
                continue
            name = str(item["name"])
            node = _node(context, kind, kind_label, name, item)
            nodes_by_name[name].append(node)
            if kind == "pathophysiology":
                pathophysiology[name] = {"item": item, "node": node, "source_index": index}
    return dict(nodes_by_name), pathophysiology


def _resolve_target(context, name, nodes_by_name):
    candidates = nodes_by_name.get(name) or []
    if len(candidates) == 1:
        return {**candidates[0], "distance": 1}
    if candidates:
        return _node(
            context,
            "ambiguous",
            "Ambiguous source target",
            name,
            distance=1,
            resolved=False,
            status="ambiguous",
            url=False,
            candidate_kinds=strings(c.get("kind") for c in candidates),
        )
    return _node(
        context,
        "unresolved",
        "Unresolved source target",
        name,
        distance=1,
        resolved=False,
        status="missing",
        url=False,
        candidate_kinds=[],
    )


def mechanism_neighborhood(
    *, model_record, link, link_index, context, nodes_by_name, pathophysiology
) -> dict[str, Any]:
    """The explicit one-hop causal neighborhood around one modeled event."""
    focus_name = str(link["target"])
    focus_record = pathophysiology.get(focus_name)
    if focus_record:
        focus = {**focus_record["node"], "distance": 0}
    else:
        focus = _node(
            context,
            "unresolved",
            "Unresolved source target",
            focus_name,
            distance=0,
            resolved=False,
            status="missing",
            url=False,
            candidate_kinds=[],
        )
    model_id = model_record["id"]
    model_node = {
        "id": model_id,
        "kind": "animal_model",
        "kind_label": "Animal model",
        "label": model_record["name"],
        "description": model_record.get("description"),
        "url": model_record["source_record_url"],
        "resolved": True,
        "resolution_status": "resolved",
        "candidate_kinds": ["animal_model"],
        "distance": None,
        "biological_scale": None,
        "biological_scale_label": None,
        "mechanism_confidence": None,
        "mechanism_confidence_label": None,
    }
    nodes = {model_node["id"]: model_node, focus["id"]: focus}
    causal: list[dict[str, Any]] = []
    if focus_record:
        for source_name, source_record in pathophysiology.items():
            for downstream_index, edge in enumerate(
                as_list(source_record["item"].get("downstream"))
            ):
                if not isinstance(edge, dict) or not edge.get("target"):
                    continue
                target_name = str(edge["target"])
                if focus_name not in (source_name, target_name):
                    continue
                source_node = (
                    focus if source_name == focus_name else {**source_record["node"], "distance": 1}
                )
                target_node = (
                    focus
                    if target_name == focus_name
                    else _resolve_target(context, target_name, nodes_by_name)
                )
                nodes[source_node["id"]] = source_node
                nodes[target_node["id"]] = target_node
                causal_type = str(edge.get("causal_link_type") or "") or None
                causal.append(
                    {
                        "id": f"causal:{quote(str(context['id']), safe='')}:"
                        f"{source_record['source_index']}:{downstream_index}",
                        "source_id": source_node["id"],
                        "target_id": target_node["id"],
                        "kind": "causal",
                        "predicate": "causes",
                        "source_slot": "pathophysiology.downstream",
                        "label": CAUSAL_EDGE_LABELS.get(causal_type or "", "Causes"),
                        "directed": True,
                        "relationship": None,
                        "fidelity": None,
                        "causal_link_type": causal_type,
                        "causal_link_type_label": humanize(causal_type),
                        "description": edge.get("description"),
                        "evidence_count": sum(
                            isinstance(e, dict) for e in as_list(edge.get("evidence"))
                        ),
                    }
                )
    causal.sort(
        key=lambda e: (
            str(nodes[e["source_id"]]["label"]).casefold(),
            str(nodes[e["target_id"]]["label"]).casefold(),
            e["id"],
        )
    )
    relationship = str(link.get("relationship") or "NOT_SPECIFIED")
    model_edge = {
        "id": f"model-link:{link_index}:{model_id}",
        "source_id": model_id,
        "target_id": focus["id"],
        "kind": "model_mechanism",
        "predicate": None,
        "source_slot": "animal_models.modeled_mechanisms",
        "label": humanize(relationship),
        "directed": False,
        "relationship": relationship,
        "fidelity": str(link.get("fidelity") or "NOT_SPECIFIED"),
        "causal_link_type": None,
        "causal_link_type_label": None,
        "description": link.get("description"),
        "evidence_count": sum(isinstance(e, dict) for e in as_list(link.get("evidence"))),
    }
    others = sorted(
        (n for i, n in nodes.items() if i not in {model_id, focus["id"]}),
        key=lambda n: (str(n["label"]).casefold(), n["kind"], n["id"]),
    )
    return {
        "version": 1,
        "scope": "one_hop_incident_causal_edges",
        "status": "missing_focus" if not focus_record else "resolved" if causal else "isolated",
        "context_id": context["id"],
        "model_node_id": model_id,
        "focus_node_id": focus["id"],
        "source_pathograph_url": f"{context['page_url']}#pathograph",
        "nodes": [model_node, focus, *others],
        "edges": [model_edge, *causal],
    }


# --- records ---


def _publications(model: dict[str, Any], evidence: list[dict[str, Any]]) -> list[str]:
    values = [canonical_curie(str(model.get("publication") or "").strip())]
    values.extend(item.get("reference") for item in evidence)
    return strings(value for value in values if value and str(value).upper().startswith("PMID:"))


def _genetic_components(model, species, prefixes, kg) -> list[dict[str, Any]]:
    """Human genes as curated; model genes as curated or derived from KG orthology."""
    components: list[dict[str, Any]] = []
    genes = [g for g in (term(value, prefixes) for value in as_list(model.get("genes"))) if g]
    curated_alleles = [
        {"id": None, "label": str(a)} for a in strings(as_list(model.get("alleles")))
    ]
    taxon = (species or {}).get("id")
    for gene in genes:
        gene_id = gene.get("id")
        is_human = bool(gene_id and gene_id.startswith("HGNC:"))
        alleles = curated_alleles if len(genes) == 1 else []
        if gene_id and not is_human:
            # A model-organism gene recorded in `genes` (outside its HGNC binding).
            component = {
                "model_gene": {**kg.gene(gene_id), "symbol": gene["label"]},
                "model_gene_basis": "CURATED",
                "alleles": alleles,
                "human_gene": None,
                "human_gene_basis": "NOT_REPORTED",
                "correspondence": None,
            }
            derived = kg.correspondences(model_gene=gene_id)
            if derived:
                for item in derived:
                    components.append(
                        {
                            **component,
                            "human_gene": kg.gene(item["human_gene"]),
                            "human_gene_basis": "DERIVED",
                            "correspondence": item["correspondence"],
                        }
                    )
            else:
                components.append(component)
            continue
        human = (
            {**kg.gene(gene_id), "symbol": gene["label"]}
            if gene_id
            else {"id": None, "symbol": gene["label"], "taxon": None, "url": None}
        )
        derived = [
            item
            for item in (kg.correspondences(human_gene=gene_id) if gene_id and taxon else [])
            if (kg.gene(item["model_gene"]).get("taxon") or {}).get("id") == taxon
        ]
        if not derived:
            components.append(
                {
                    "model_gene": None,
                    "model_gene_basis": "NOT_REPORTED",
                    "alleles": alleles,
                    "human_gene": human,
                    "human_gene_basis": "CURATED",
                    "correspondence": None,
                }
            )
        for item in derived:
            components.append(
                {
                    "model_gene": kg.gene(item["model_gene"]),
                    "model_gene_basis": "DERIVED",
                    "alleles": alleles,
                    "human_gene": human,
                    "human_gene_basis": "CURATED",
                    "correspondence": item["correspondence"],
                }
            )
    if not genes and curated_alleles:
        components.append(
            {
                "model_gene": None,
                "model_gene_basis": "NOT_REPORTED",
                "alleles": curated_alleles,
                "human_gene": None,
                "human_gene_basis": "NOT_REPORTED",
                "correspondence": None,
            }
        )
    return components


def dismech_model_record(
    model, index, context, entry, prefixes, kg, index_cache, used_ids
) -> dict[str, Any] | None:
    label = animal_model_label(model)
    if not label:
        return None
    name_basis = "CURATED" if str(model.get("name") or "").strip() else "DERIVED"
    record_id = f"dismech:{context['source_path']}:{label}"
    if record_id in used_ids:
        record_id = f"{record_id}:{index + 1}"
    used_ids.add(record_id)
    model_url = (
        f"{context['page_url']}#{anchor('animal-model', label)}"
        if context["kind"] == "Disorder"
        else f"{context['page_url']}#animal-models"
    )
    species = normalize_species(model.get("species"))
    category = normalize_category(model.get("category"))
    record: dict[str, Any] = {
        "id": record_id,
        "name": label,
        "name_basis": name_basis,
        "description": model.get("description"),
        "source": "DISMECH",
        **species,
        "strain_background": str(model["background"]).strip() if model.get("background") else None,
        **category,
        "genotype": {"id": None, "label": str(model["genotype"]).strip()}
        if model.get("genotype")
        else None,
        "source_record_url": model_url,
        "source_yaml_url": context["source_yaml_url"],
        "context_id": context["id"],
        "context_kind": context["kind"],
        "context_name": context["name"],
        "notes": model.get("notes"),
    }
    record["genetic_components"] = [
        {"reagents": [], "source": "DisMech", **component}
        for component in _genetic_components(model, species["species"], prefixes, kg)
    ]

    if context["id"] not in index_cache:
        index_cache[context["id"]] = pathograph_index(entry, context)
    nodes_by_name, pathophysiology = index_cache[context["id"]]
    mechanisms: list[dict[str, Any]] = []
    link_evidence: list[dict[str, Any]] = []
    for link_index, link in enumerate(as_list(model.get("modeled_mechanisms"))):
        if not isinstance(link, dict) or not link.get("target"):
            continue
        evidence = normalize_evidence(link.get("evidence"), prefixes)
        link_evidence = merge_evidence(link_evidence, evidence)
        relationship = str(link.get("relationship") or "NOT_SPECIFIED")
        fidelity = str(link.get("fidelity") or "NOT_SPECIFIED")
        mechanisms.append(
            {
                "target": str(link["target"]),
                "target_url": _node_url(context, "pathophysiology", str(link["target"])),
                "relationship": relationship,
                "relationship_label": humanize(relationship),
                "fidelity": fidelity,
                "fidelity_label": humanize(fidelity),
                "model_scale": link.get("model_scale"),
                "description": link.get("description"),
                "limitations": link.get("limitations"),
                "evidence": evidence,
                "evidence_count": len(evidence),
                "neighborhood": mechanism_neighborhood(
                    model_record=record,
                    link=link,
                    link_index=link_index,
                    context=context,
                    nodes_by_name=nodes_by_name,
                    pathophysiology=pathophysiology,
                ),
            }
        )
    evidence = merge_evidence(normalize_evidence(model.get("evidence"), prefixes), link_evidence)
    publications = _publications(model, evidence)
    disease_associations = []
    if context.get("mondo"):
        disease_associations.append(
            {
                "disease": {
                    "id": context["mondo"],
                    # MONDO's label, so both sources name a disease the same way.
                    "label": kg.disease_labels.get(context["mondo"]) or context["name"],
                    "url": expand_curie(context["mondo"], prefixes),
                },
                "original_disease": None,
                "source": "infores:dismech",
                "publications": publications,
                "evidence_codes": [],
            }
        )
    phenotypes = [
        t for t in (term(v, prefixes) for v in as_list(model.get("associated_phenotypes"))) if t
    ]
    record.update(
        {
            "disease_associations": disease_associations,
            "modeled_mechanisms": mechanisms,
            "associated_phenotypes": phenotypes,
            "publications": publications,
            "evidence": evidence,
        }
    )
    return record


def dismech_records(source: Path, provenance: dict[str, Any], prefixes: dict[str, str], kg):
    """All DisMech animal model records plus one context row per disorder entry."""
    models: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []
    warnings: list[str] = []
    for source_kind, path in entry_files(source):
        relative = path.relative_to(source)
        has_models = _has_animal_models(path)
        if source_kind == "Module" and not has_models:
            continue
        data = load_yaml(path)
        context = context_metadata(data, source_kind, relative, provenance)
        context["pathophysiology_names"] = [
            str(item["name"])
            for item in as_list(data.get("pathophysiology"))
            if isinstance(item, dict) and item.get("name")
        ]
        contexts.append(context)
        if not has_models:
            continue
        index_cache: dict[str, Any] = {}
        used_ids: set[str] = set()
        for index, raw in enumerate(as_list(data.get("animal_models"))):
            if not isinstance(raw, dict):
                continue
            record = dismech_model_record(
                raw, index, context, data, prefixes, kg, index_cache, used_ids
            )
            if record:
                models.append(record)
            else:
                warnings.append(
                    f"Skipped animal model with no name, genotype, or species in {relative}"
                )
    return models, contexts, warnings
