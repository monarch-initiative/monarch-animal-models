"""Relate DisMech and KG records: disease precision, counterparts, and coverage.

Every relation here is computed from curated links (MONDO subclass axioms,
shared publications, normalized species, curated or KG-derived genes) and is
labeled with the rule that produced it. Nothing is scored or ranked.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

# An ancestor disease counts as a "close parent" only when it is this specific.
# Without a cutoff, single KG models of umbrella terms ("syndromic disease",
# "metabolic disease") would make nearly every DisMech entry look covered.
CLOSE_PARENT_MAX_DESCENDANTS = 20

RELATION_ORDER = ("SAME", "KG_MORE_SPECIFIC", "KG_MORE_GENERAL", "UNRELATED", "NOT_COMPARABLE")

PRECISION_LABELS = {
    "EXACT": "Exact DisMech entry",
    "MORE_SPECIFIC": "More specific than a DisMech entry",
    "NO_DISMECH_ENTRY": "No DisMech entry",
}

RELATION_LABELS = {
    "SAME": "Same disease",
    "KG_MORE_SPECIFIC": "KG disease more specific",
    "KG_MORE_GENERAL": "KG disease more general",
    "UNRELATED": "Unrelated diseases",
    "NOT_COMPARABLE": "No DisMech disease to compare",
}

BASIS_LABELS = {
    "SAME_PUBLICATION": "Same publication",
    "SAME_DISEASE": "Same disease",
    "RELATED_DISEASE": "Related disease (MONDO subclass path)",
    "SAME_SPECIES": "Same species",
    "SAME_HUMAN_GENE": "Same human gene",
}

COVERAGE_LABELS = {
    "BOTH": "Models in DisMech and KG",
    "DISMECH_ONLY": "DisMech models only",
    "KG_ONLY": "KG models only",
    "KG_SUBTYPE_ONLY": "No exact models; KG has subtype models",
    "KG_CLOSE_PARENT_ONLY": "No exact models; KG has close-parent models",
    "DISMECH_AND_KG_OTHER_PRECISION": "DisMech models; KG only at another precision",
    "NONE": "No models",
}


def _nearest(candidates: set[str], kg) -> list[str]:
    """Candidates that are not an ancestor of another candidate."""
    return sorted(
        c for c in candidates if not any(c in kg.ancestors.get(o, ()) for o in candidates if o != c)
    )


def annotate_kg_precision(kg_models: list[dict[str, Any]], contexts_by_mondo, kg) -> None:
    """Place each KG disease association relative to DisMech entries."""
    entry_ids = set(contexts_by_mondo)
    for model in kg_models:
        for association in model["disease_associations"]:
            disease = association["disease"]["id"]
            if disease in entry_ids:
                association["dismech_precision"] = "EXACT"
                association["dismech_entries"] = [c["id"] for c in contexts_by_mondo[disease]]
                continue
            ancestors = kg.ancestors.get(disease, set()) & entry_ids
            if ancestors:
                association["dismech_precision"] = "MORE_SPECIFIC"
                association["dismech_entries"] = [
                    c["id"] for mondo in _nearest(ancestors, kg) for c in contexts_by_mondo[mondo]
                ]
            else:
                association["dismech_precision"] = "NO_DISMECH_ENTRY"
                association["dismech_entries"] = []


def disease_relation(dismech_disease: str | None, kg_disease: str, kg) -> str:
    if not dismech_disease:
        return "NOT_COMPARABLE"
    if dismech_disease == kg_disease:
        return "SAME"
    if dismech_disease in kg.ancestors.get(kg_disease, ()):
        return "KG_MORE_SPECIFIC"
    if kg_disease in kg.ancestors.get(dismech_disease, ()):
        return "KG_MORE_GENERAL"
    return "UNRELATED"


def _is_close(dismech_disease: str, kg_disease: str, relation: str, kg) -> bool:
    if relation in {"SAME", "KG_MORE_SPECIFIC"}:
        return True
    return relation == "KG_MORE_GENERAL" and (
        kg.descendant_counts.get(kg_disease, 0) <= CLOSE_PARENT_MAX_DESCENDANTS
    )


def _human_genes(model: dict[str, Any]) -> set[str]:
    return {
        component["human_gene"]["id"]
        for component in model.get("genetic_components") or []
        if (component.get("human_gene") or {}).get("id")
    }


def _dismech_disease(model: dict[str, Any]) -> str | None:
    associations = model.get("disease_associations") or []
    return associations[0]["disease"]["id"] if associations else None


def find_counterparts(dismech_models, kg_models, kg) -> list[dict[str, Any]]:
    """Candidate DisMech-KG pairs.

    A pair is a candidate when the two records cite a common publication, or
    when they share species and human gene and their diseases are the same or
    closely related. Every rule that holds is listed in `bases`.
    """
    by_publication: dict[str, set[str]] = defaultdict(set)
    by_gene_species: dict[tuple[str, str], set[str]] = defaultdict(set)
    kg_by_id = {model["id"]: model for model in kg_models}
    for model in kg_models:
        for publication in model["publications"]:
            by_publication[publication].add(model["id"])
        taxon = (model.get("species") or {}).get("id")
        for gene in _human_genes(model):
            if taxon:
                by_gene_species[(gene, taxon)].add(model["id"])

    counterparts: list[dict[str, Any]] = []
    for model in dismech_models:
        dismech_disease = _dismech_disease(model)
        taxon = (model.get("species") or {}).get("id")
        genes = _human_genes(model)
        candidates: set[str] = set()
        for publication in model["publications"]:
            candidates |= by_publication.get(publication, set())
        gene_candidates: set[str] = set()
        if taxon:
            for gene in genes:
                gene_candidates |= by_gene_species.get((gene, taxon), set())
        for kg_id in sorted(candidates | gene_candidates):
            other = kg_by_id[kg_id]
            shared = sorted(set(model["publications"]) & set(other["publications"]))
            relations = [
                (disease_relation(dismech_disease, a["disease"]["id"], kg), a["disease"]["id"])
                for a in other["disease_associations"]
            ]
            relation, kg_disease = min(relations, key=lambda item: RELATION_ORDER.index(item[0]))
            close = dismech_disease is not None and _is_close(
                dismech_disease, kg_disease, relation, kg
            )
            same_gene = bool(genes & _human_genes(other))
            same_species = bool(taxon) and taxon == (other.get("species") or {}).get("id")
            if not shared and not (same_gene and same_species and close):
                continue
            bases = []
            if shared:
                bases.append("SAME_PUBLICATION")
            if relation == "SAME":
                bases.append("SAME_DISEASE")
            elif relation in {"KG_MORE_SPECIFIC", "KG_MORE_GENERAL"}:
                bases.append("RELATED_DISEASE")
            if same_species:
                bases.append("SAME_SPECIES")
            if same_gene:
                bases.append("SAME_HUMAN_GENE")
            counterparts.append(
                {
                    "id": f"counterpart:{model['id']}|{kg_id}",
                    "dismech_model_id": model["id"],
                    "kg_model_id": kg_id,
                    "bases": bases,
                    "basis_labels": [BASIS_LABELS[b] for b in bases],
                    "shared_publications": shared,
                    "disease_relation": relation,
                    "disease_relation_label": RELATION_LABELS[relation],
                    "species_agreement": same_species,
                }
            )
    return counterparts


def _coverage(has_dismech: bool, exact: bool, subtype: bool, parent: bool) -> str:
    if has_dismech:
        if exact:
            return "BOTH"
        return "DISMECH_AND_KG_OTHER_PRECISION" if subtype or parent else "DISMECH_ONLY"
    if exact:
        return "KG_ONLY"
    if subtype:
        return "KG_SUBTYPE_ONLY"
    return "KG_CLOSE_PARENT_ONLY" if parent else "NONE"


def _species_labels(models: list[dict[str, Any]]) -> list[str]:
    return sorted({(m.get("species") or {}).get("label") for m in models} - {None})


def coverage_rows(contexts, dismech_models, kg_models, kg) -> list[dict[str, Any]]:
    """One row per DisMech disorder entry, plus one per KG disease with no exact entry."""
    kg_by_disease: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for model in kg_models:
        for disease in {a["disease"]["id"] for a in model["disease_associations"]}:
            kg_by_disease[disease].append(model)
    dismech_by_context: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for model in dismech_models:
        dismech_by_context[model["context_id"]].append(model)
    entry_ids = {c["mondo"] for c in contexts if c["kind"] == "Disorder" and c.get("mondo")}
    context_by_id = {c["id"]: c for c in contexts}

    def kg_sets(mondo: str | None):
        if not mondo:
            return [], [], []
        exact = kg_by_disease.get(mondo, [])
        subtype = [m for d in kg.descendants.get(mondo, ()) for m in kg_by_disease.get(d, [])]
        parent = [
            m
            for d in kg.ancestors.get(mondo, ())
            if kg.descendant_counts.get(d, 0) <= CLOSE_PARENT_MAX_DESCENDANTS
            for m in kg_by_disease.get(d, [])
        ]
        return exact, subtype, parent

    def mechanism_coverage(context, models):
        names = context.get("pathophysiology_names") or []
        by_target: dict[str, set[str]] = defaultdict(set)
        for model in models:
            for link in model["modeled_mechanisms"]:
                by_target[link["target"]].add(link["relationship"])
        recapitulated = [n for n in names if by_target.get(n, set()) & {"RECAPITULATES"}]
        partial = [
            n
            for n in names
            if n not in recapitulated and by_target.get(n, set()) & {"PARTIALLY_RECAPITULATES"}
        ]
        other = [n for n in names if n in by_target and n not in recapitulated and n not in partial]
        return {
            "mechanism_node_count": len(names),
            "mechanisms_recapitulated": recapitulated,
            "mechanisms_partially_recapitulated": partial,
            "mechanisms_other_links_only": other,
            "mechanisms_without_animal_model_link": [n for n in names if n not in by_target],
        }

    def row(row_id, name, mondo, context, dismech, exact, subtype, parent):
        unique = lambda models: sorted({m["id"] for m in models})  # noqa: E731
        coverage = _coverage(bool(dismech), bool(exact), bool(subtype), bool(parent))
        kg_all = {m["id"]: m for m in [*exact, *subtype, *parent]}.values()
        nearest = []
        if not context and mondo:
            nearest = [
                c["id"]
                for d in _nearest(kg.ancestors.get(mondo, set()) & entry_ids, kg)
                for c in contexts
                if c.get("mondo") == d
            ]
        result = {
            "id": row_id,
            "name": name,
            "disease": {"id": mondo, "label": name} if mondo else None,
            "dismech_entry": context["id"] if context else None,
            "dismech_entry_status": "Has DisMech entry" if context else "No DisMech entry",
            "dismech_page_url": context["page_url"] if context else None,
            "nearest_dismech_entries": nearest,
            "nearest_dismech_entry_names": [context_by_id[i]["name"] for i in nearest],
            "dismech_model_ids": unique(dismech),
            "kg_exact_model_ids": unique(exact),
            "kg_subtype_model_ids": unique(subtype),
            "kg_close_parent_model_ids": unique(parent),
            "dismech_model_count": len(unique(dismech)),
            "kg_exact_model_count": len(unique(exact)),
            "kg_subtype_model_count": len(unique(subtype)),
            "kg_close_parent_model_count": len(unique(parent)),
            "coverage": coverage,
            "coverage_label": COVERAGE_LABELS[coverage],
            "dismech_species": _species_labels(dismech),
            "kg_species": _species_labels(list(kg_all)),
            "kg_sources": sorted({m["source"] for m in kg_all}),
            "synonyms": context.get("synonyms", []) if context else [],
        }
        if context:
            result.update(mechanism_coverage(context, dismech))
        else:
            result.update(
                {
                    "mechanism_node_count": 0,
                    "mechanisms_recapitulated": [],
                    "mechanisms_partially_recapitulated": [],
                    "mechanisms_other_links_only": [],
                    "mechanisms_without_animal_model_link": [],
                }
            )
        result["mechanism_coverage_label"] = (
            "No pathophysiology nodes"
            if not result["mechanism_node_count"]
            else "Some nodes recapitulated"
            if result["mechanisms_recapitulated"]
            else "Partial recapitulation only"
            if result["mechanisms_partially_recapitulated"]
            else "Linked, not recapitulated"
            if result["mechanisms_other_links_only"]
            else "No animal model links"
        )
        return result

    rows = []
    for context in contexts:
        if context["kind"] != "Disorder":
            continue
        mondo = context.get("mondo")
        rows.append(
            row(
                context["id"],
                context["name"],
                mondo,
                context,
                dismech_by_context.get(context["id"], []),
                *kg_sets(mondo),
            )
        )
    for disease in sorted(set(kg_by_disease) - entry_ids):
        if not disease.startswith("MONDO:"):
            continue
        rows.append(
            row(
                f"kg-disease:{disease}",
                kg.disease_labels.get(disease) or disease,
                disease,
                None,
                [],
                *kg_sets(disease),
            )
        )
    rows.sort(key=lambda r: r["name"].casefold())
    return rows
