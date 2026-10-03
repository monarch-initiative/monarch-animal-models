# Monarch Animal Models

Monarch Animal Models is a generated, read-only catalog and browser of
whole-organism disease models from two curated sources:

- **DisMech** `animal_models` entries, with their links into DisMech
  pathographs (`modeled_mechanisms`), evidence, and publications.
- **Alliance member genotype-to-disease assertions** (MGI, ZFIN, RGD) carried
  by the [Monarch KG](https://monarchinitiative.org/) as `biolink:model_of`
  edges, with each genotype's alleles and genes.
- **ZFIN fish components** ([`fish_components_fish.txt`](https://zfin.org/downloads)),
  which supply the genes targeted by morpholinos, CRISPRs, and TALENs. The KG
  has no nodes for these reagents, so without this file 40% of ZFIN fish reach
  no gene. Each gene link records which source supplied it.

It is a sibling of [monarch-nams](https://github.com/monarch-initiative/monarch-nams)
and follows the same pattern: copy sources with provenance, normalize into
reusable JSON, validate, and package a static faceted browser. There is no
database, server, or curation surface in this repository.

## What it shows

- **Animal models.** Every record from either source in one shape, with
  facets for source, species, model category, disease, human gene, model
  organism gene, pathograph relationship, and counterpart status.
- **Where the sources meet.** Each KG disease association is placed relative
  to DisMech entries (exact entry, more specific than an entry, or no entry).
  DisMech and KG records that cite the same publication, or share species and
  human gene with the same or a closely related disease, are linked as
  *counterpart candidates*, labeled with every rule that matched.

The catalog JSON also carries **disease coverage rows** (not shown in the
browser): one per DisMech disorder entry, plus each KG model disease with no
exact DisMech entry, counting exact-disease models, KG models of subtypes, and
KG models of close parents. They serve as curation worklists, for example
DisMech entries with KG models but none curated in DisMech.

## Destination model

[`schema/monarch_animal_models.yaml`](schema/monarch_animal_models.yaml) is the
LinkML model for the catalog. Its `AnimalModel` extends NAMO `AnimalModel`
(species, strain, life stage, environment) with genetic composition. The
**human disease gene**, the **model organism gene**, and the
**correspondence** between them are separate, because the relationship is not
always one-to-one orthology: zebrafish co-orthologs, human transgenes,
syntenic-region models, and models with no gene at all.

Every derived value carries a `ValueBasis`:

| Basis | Meaning |
|---|---|
| `CURATED` | Stated by the source record |
| `SAFE_MAPPING` | Mapped from curated free text by a documented exact-match table |
| `DERIVED` | Computed by this pipeline from another source (KG orthology) |
| `NOT_REPORTED` | The source recorded nothing |
| `NOT_NORMALIZED` | The source recorded a value no documented rule maps |

Orthology support currently comes from the Monarch KG (PANTHER families, and
ZFIN and Xenbase ortholog assertions). `OrthologySupport` reserves method-count
and best-score fields so a richer source, such as Alliance combined orthology,
can be layered in without a schema change.

Catalog records also carry flat, facet-friendly fields (`*_label`,
`*_symbols`, `counterpart_status`, …) derived from the schema fields for the
browser.

## Quick start

```bash
just build    # sync DisMech, slice the KG, download ZFIN components, build dist/
just check    # build, validate, and test
just serve    # http://localhost:4174
```

The default layout assumes sibling clones at `../dismech` and `../namo`.
Set `DISMECH_ROOT` and `NAMO_ROOT` (a `.env` file works) when they live
elsewhere.

`just kg-slice` queries the latest published Monarch KG DuckDB file over HTTPS;
DuckDB reads only the pages the queries touch, so this takes well under a minute
without downloading the 6.7 GB file. Set `KG_DUCKDB` to a local file to use
that instead (and `KG_VERSION` to record its release).

## Pipeline

```text
DisMech kb/disorders, kb/modules ── sync ──► .cache/dismech (+ provenance.json)
Monarch KG DuckDB (remote or local) ─ kg-slice ─► .cache/kg (gzipped TSVs + provenance.json)
ZFIN fish_components_fish.txt ─── zfin-sync ──► .cache/zfin (gzipped + provenance.json)
NAMO schema ───────────────────────────────────┐
                                                ▼
                      build ──► dist/data/catalog.json   full reusable catalog
                                dist/data/index.js       slim records for search and facets
                                dist/data/details/*.js   full records, loaded on demand
                                dist/index.html, assets/
```

`build` refuses to run if any copied input no longer matches its checksum.
`validate` fails closed on inconsistent output: dangling ids, counterparts not
linked from both sides, bases that disagree with the evidence, and counts
that disagree with the records.

## Rules

These are the only places the pipeline turns one value into another:

- **Species** and **model category**: exact-match tables in
  [`normalize.py`](src/animal_models_browser/normalize.py). Multi-species
  values ("Mouse and rat") and compound descriptions stay raw.
- **Disease precision**: MONDO `rdfs:subClassOf` closure from the KG. An
  ancestor counts as a *close parent* only when it has at most
  `CLOSE_PARENT_MAX_DESCENDANTS` (20) MONDO descendants; without a cutoff,
  single KG models of umbrella terms would make nearly every entry look covered.
- **Genes for ZFIN fish**: the KG's allele -> gene links, completed from ZFIN
  fish components for reagent-targeted genes and alleles the KG leaves
  without a gene. Gene symbols are never parsed out of fish names.
- **Model genes for DisMech records**: KG orthologs of the curated HGNC gene,
  restricted to the record's normalized species, labeled `DERIVED`.
- **Counterpart candidates**: same publication; or same species, same human
  gene, and the same disease, a subtype, or a close parent.

Nothing is scored, ranked, or inferred from missing metadata.

## Source disclaimer

DisMech is an AI-curated research resource and is not medical advice. See the
[full DisMech disclaimer](https://dismech.monarchinitiative.org/elements/disclaimer/).

## License

BSD-3-Clause. Source data retains the licensing and attribution of DisMech,
the Monarch KG and its upstream sources, and NAMO.
