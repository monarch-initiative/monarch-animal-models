"""Command-line entry points for the Monarch Animal Models pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from .kg_source import KG_RELEASE_URL, slice_kg


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="animal-models-browser",
        description="Build the static Monarch Animal Models catalog and browser.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="Copy required DisMech source files")
    sync_parser.add_argument("--dismech-root", type=Path, required=True)
    sync_parser.add_argument("--output", type=Path, required=True)

    slice_parser = subparsers.add_parser("kg-slice", help="Extract the Monarch KG slice")
    slice_parser.add_argument(
        "--kg", default=KG_RELEASE_URL, help="KG DuckDB path or URL (default: latest release)"
    )
    slice_parser.add_argument("--output", type=Path, required=True)

    zfin_parser = subparsers.add_parser("zfin-sync", help="Download ZFIN fish components")
    zfin_parser.add_argument("--output", type=Path, required=True)

    build_parser = subparsers.add_parser("build", help="Generate the catalog and static site")
    build_parser.add_argument("--dismech", type=Path, required=True)
    build_parser.add_argument("--kg", type=Path, required=True)
    build_parser.add_argument("--zfin", type=Path, required=True)
    build_parser.add_argument("--namo-schema", type=Path, required=True)
    build_parser.add_argument("--output", type=Path, required=True)

    serve_parser = subparsers.add_parser("serve", help="Preview dist/ with gzip on IPv4 and IPv6")
    serve_parser.add_argument("--site", type=Path, default=Path("dist"))
    serve_parser.add_argument("--port", type=int, default=4174)

    validate_parser = subparsers.add_parser("validate", help="Validate generated output")
    validate_parser.add_argument("--site", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "sync":
        from .dismech_source import sync_dismech

        manifest = sync_dismech(args.dismech_root, args.output)
        print(
            f"Copied {manifest['file_count']} YAML files from DisMech "
            f"{manifest['dismech']['revision'][:12]}"
        )
        return 0
    if args.command == "kg-slice":
        manifest = slice_kg(args.kg, args.output)
        rows = ", ".join(
            f"{item['path'].split('.')[0]}={item['rows']}" for item in manifest["files"]
        )
        print(f"Extracted KG {manifest['version']} slice: {rows}")
        return 0
    if args.command == "zfin-sync":
        from .zfin_source import sync_zfin

        manifest = sync_zfin(args.output)
        print(f"Downloaded {manifest['files'][0]['rows']} ZFIN fish component rows")
        return 0
    if args.command == "build":
        from .pipeline import build_site

        catalog = build_site(args.dismech, args.kg, args.zfin, args.namo_schema, args.output)
        stats = catalog["stats"]
        print(
            f"Built {stats['model_count']} model records "
            f"({stats['dismech_model_count']} DisMech, {stats['kg_model_count']} KG), "
            f"{stats['disease_count']} disease rows, and "
            f"{stats['counterpart_count']} counterpart candidates in {args.output}"
        )
        return 0
    if args.command == "serve":
        from .serve import serve

        serve(args.site, args.port)
        return 0
    if args.command == "validate":
        from .pipeline import validate_site

        report = validate_site(args.site)
        print(
            f"Validated {report['model_count']} models and {report['disease_count']} disease rows"
        )
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
