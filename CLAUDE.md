# CLAUDE.md

Monarch Animal Models is a generated, read-only catalog and faceted browser of
whole-organism disease models from DisMech `animal_models` and Alliance member
(MGI, ZFIN, RGD) `model_of` assertions in the Monarch KG. NAMO supplies the
`AnimalModel` class the destination schema extends. See AGENTS.md for the
boundaries.

## Commands

```bash
just build       # sync DisMech, slice the KG, generate dist/
just test        # Python and JavaScript tests
just check       # build, validate, and test
just serve       # serve dist/ at http://localhost:4174
just lint-schema # linkml-lint the destination schema
```

Set `DISMECH_ROOT`, `NAMO_ROOT`, or `KG_DUCKDB` when the inputs are elsewhere.

## Layout

- `schema/monarch_animal_models.yaml`: destination LinkML model
- `src/animal_models_browser/`: `dismech_source.py` (sync, records,
  neighborhoods), `kg_source.py` (slice, records), `normalize.py` (safe
  mappings), `matching.py` (precision, counterparts, coverage), `pipeline.py`
  (build, validate)
- `config/*.browser.json`: facets, search fields, and sorts per view
- `src/site/`: static browser; `core.js` is shared with monarch-nams
