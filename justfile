set dotenv-load := true

dismech_root := env_var_or_default("DISMECH_ROOT", "../dismech")
namo_root := env_var_or_default("NAMO_ROOT", "../namo")
kg := env_var_or_default("KG_DUCKDB", "https://data.monarchinitiative.org/monarch-kg/latest/monarch-kg.duckdb")
run := "uv run python -m animal_models_browser.cli"

default: check

# Copy the DisMech YAML this build reads into .cache/dismech
sync:
    PYTHONPATH=src {{run}} sync --dismech-root "{{dismech_root}}" --output .cache/dismech

# Extract the KG slice into .cache/kg (remote release by default; set KG_DUCKDB for a local file)
kg-slice:
    PYTHONPATH=src {{run}} kg-slice --kg "{{kg}}" --output .cache/kg

# Download ZFIN fish components into .cache/zfin (reagent and allele -> gene links)
zfin-sync:
    PYTHONPATH=src {{run}} zfin-sync --output .cache/zfin

build-site:
    PYTHONPATH=src {{run}} build --dismech .cache/dismech --kg .cache/kg --zfin .cache/zfin --namo-schema "{{namo_root}}/src/namo/schema/namo.yaml" --output dist

build: sync kg-slice zfin-sync build-site

validate:
    PYTHONPATH=src {{run}} validate --site dist

# Check the destination schema with LinkML
lint-schema:
    uv run --group dev linkml-lint schema/monarch_animal_models.yaml

test-python:
    PYTHONPATH=src uv run python -m unittest discover -s tests -p 'test_*.py' -v

test-js:
    node --test tests/*.test.mjs

test: test-python test-js

check: build validate test

# Preview dist/ with gzip (like GitHub Pages) on IPv4 and IPv6
serve:
    PYTHONPATH=src {{run}} serve --site dist --port 4174
