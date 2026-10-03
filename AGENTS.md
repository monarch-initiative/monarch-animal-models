# Monarch Animal Models agent instructions

This repository is a read-only presentation and transformation layer over
DisMech, the Monarch KG, and NAMO. It contains no independently curated model
records.

- Never hand-edit generated files under `.cache/` or `dist/`.
- Never copy source YAML or KG exports into a committed directory. Run
  `just sync`, `just kg-slice`, or `just build`; the pipeline records source
  revisions and checksums.
- Fix scientific content in DisMech, the KG's upstream sources, or NAMO, not
  here. Changes in this repo should be limited to extraction and matching
  rules, the destination schema, browser configuration, tests, UI, and
  deployment.
- Keep DisMech and KG records separate. A counterpart is a candidate labeled
  with its matching rules, never an identity assertion.
- Preserve explicit missing values. Do not infer species, categories, genes,
  or disease links beyond the documented rules in `normalize.py` and
  `matching.py`, and label every derived value with its `ValueBasis`.
- Keep the output fully static and base-path agnostic for GitHub Pages.
- Run `just check` before committing.
