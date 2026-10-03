"""Shared helpers for provenance, safe file handling, and value normalization.

Several of these are adapted from monarch-nams so the two browsers handle
provenance and missing values the same way.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

DISMECH_REPOSITORY = "https://github.com/monarch-initiative/dismech"
DISMECH_SITE = "https://dismech.monarchinitiative.org"
NAMO_REPOSITORY = "https://github.com/monarch-initiative/namo"
NAMO_DOCS = "https://monarch-initiative.github.io/namo/elements"
MONARCH_SITE = "https://monarchinitiative.org"

# Expansions for identifiers that come from the KG rather than the DisMech
# schema's prefix map.
KG_PREFIXES = {
    "MONDO": "http://purl.obolibrary.org/obo/MONDO_",
    "DOID": "http://purl.obolibrary.org/obo/DOID_",
    "NCBITaxon": "http://purl.obolibrary.org/obo/NCBITaxon_",
    "HGNC": "https://www.genenames.org/data/gene-symbol-report/#!/hgnc_id/HGNC:",
    "MGI": "https://www.informatics.jax.org/accession/MGI:",
    "ZFIN": "https://zfin.org/",
    "RGD": "https://rgd.mcw.edu/rgdweb/report/gene/main.html?id=",
    "PMID": "https://pubmed.ncbi.nlm.nih.gov/",
    "NCBIGene": "https://www.ncbi.nlm.nih.gov/gene/",
    "FB": "https://flybase.org/reports/",
    "WB": "https://wormbase.org/species/c_elegans/gene/",
    "Xenbase": "https://www.xenbase.org/entry/",
}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _https_remote(remote: str | None, fallback_repository: str) -> str:
    if not remote:
        return fallback_repository
    if remote.startswith("git@github.com:"):
        remote = "https://github.com/" + remote.removeprefix("git@github.com:")
    if remote.startswith("ssh://git@github.com/"):
        remote = "https://github.com/" + remote.removeprefix("ssh://git@github.com/")
    return remote.removesuffix(".git")


def git_root(path: Path) -> Path | None:
    value = run_git(path.parent if path.is_file() else path, "rev-parse", "--show-toplevel")
    return Path(value) if value else None


def git_metadata(root: Path, fallback_repository: str) -> dict[str, Any]:
    top_level = run_git(root, "rev-parse", "--show-toplevel")
    if not top_level or Path(top_level).resolve() != root.resolve():
        return {
            "revision": "unknown",
            "branch": "unknown",
            "dirty": False,
            "repository": fallback_repository,
        }
    return {
        "revision": run_git(root, "rev-parse", "HEAD") or "unknown",
        "branch": run_git(root, "branch", "--show-current") or "detached",
        "dirty": bool(run_git(root, "status", "--porcelain")),
        "repository": _https_remote(
            run_git(root, "remote", "get-url", "origin"), fallback_repository
        ),
    }


def replace_directory(path: Path) -> None:
    resolved = path.resolve()
    protected = {Path("/").resolve(), Path.home().resolve(), Path(Path.cwd().anchor).resolve()}
    if resolved in protected or len(resolved.parts) < 4:
        raise ValueError(f"Refusing to replace unsafe output directory: {resolved}")
    if path.exists():
        if not path.is_dir():
            raise ValueError(f"Output path exists and is not a directory: {path}")
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_yaml(path: Path) -> dict[str, Any]:
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    value = yaml.load(path.read_text(encoding="utf-8"), Loader=loader)  # noqa: S506
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return value


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(value, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def strings(values: Iterable[Any]) -> list[str]:
    """Distinct, case-insensitively de-duplicated, non-empty strings in input order."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def humanize(value: str | None) -> str | None:
    if not value:
        return None
    text = value.replace("_", " ").strip()
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    return text.capitalize()


def slugify_page(name: str) -> str:
    return name.replace(" ", "_").replace("/", "_").replace("(", "").replace(")", "")


def anchor(prefix: str, value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return f"{prefix}-{slug or 'item'}"


def prefix_map(schema: dict[str, Any]) -> dict[str, str]:
    result = dict(KG_PREFIXES)
    for prefix, definition in (schema.get("prefixes") or {}).items():
        if isinstance(definition, str):
            result[str(prefix)] = definition
        elif isinstance(definition, dict):
            reference = definition.get("prefix_reference") or definition.get("@id")
            if reference:
                result[str(prefix)] = str(reference)
    return result


def expand_curie(value: str | None, prefixes: dict[str, str]) -> str | None:
    if not value:
        return None
    if value.startswith(("https://", "http://")):
        return value
    if ":" not in value:
        return None
    prefix, local = value.split(":", 1)
    base = prefixes.get(prefix) or prefixes.get(prefix.lower()) or prefixes.get(prefix.upper())
    return f"{base}{quote(local, safe=':/#?=&-')}" if base else None


def canonical_curie(value: str | None) -> str | None:
    """Upper-case the prefix of identifiers whose canonical prefix is upper case.

    DisMech writes HGNC identifiers as ``hgnc:1234``; the KG writes ``HGNC:1234``.
    """
    if not value or ":" not in value:
        return value
    prefix, local = value.split(":", 1)
    if prefix.lower() in {"hgnc", "mondo", "pmid", "mgi", "zfin", "rgd", "ncbitaxon"}:
        canonical = {"ncbitaxon": "NCBITaxon"}.get(prefix.lower(), prefix.upper())
        return f"{canonical}:{local}"
    return value


def term(value: Any, prefixes: dict[str, str]) -> dict[str, Any] | None:
    """Normalize a DisMech descriptor (``preferred_term`` plus optional ``term``)."""
    if not isinstance(value, dict):
        return None
    term_value = value.get("term") if isinstance(value.get("term"), dict) else value
    identifier = canonical_curie(term_value.get("id")) if isinstance(term_value, dict) else None
    canonical = term_value.get("label") if isinstance(term_value, dict) else None
    label = value.get("preferred_term") or canonical or value.get("name")
    if not label and not identifier:
        return None
    return {
        "id": str(identifier) if identifier else None,
        "label": str(label or identifier),
        "url": expand_curie(str(identifier), prefixes) if identifier else None,
    }


def normalize_evidence(values: Any, prefixes: dict[str, str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for value in as_list(values):
        if not isinstance(value, dict):
            continue
        reference = canonical_curie(str(value.get("reference") or "").strip()) or ""
        snippet = str(value.get("snippet") or "").strip()
        key = (reference.casefold(), snippet.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "reference": reference or None,
                "reference_url": expand_curie(reference, prefixes),
                "reference_title": value.get("reference_title"),
                "supports": value.get("supports"),
                "evidence_source": value.get("evidence_source"),
                "snippet": snippet or None,
                "explanation": value.get("explanation"),
            }
        )
    return result


def merge_evidence(*collections: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in (item for collection in collections for item in collection):
        key = (
            str(item.get("reference") or "").casefold(),
            str(item.get("snippet") or "").casefold(),
        )
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
