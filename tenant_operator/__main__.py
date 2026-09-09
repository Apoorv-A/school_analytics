"""Validate and apply tenant configuration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from tenant_config.models import TenantConfigDocument


def load_document(path: Path) -> TenantConfigDocument:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return TenantConfigDocument.model_validate(raw)


def validate(path: Path) -> list[str]:
    try:
        load_document(path)
    except Exception as exc:
        return [str(exc)]
    return []


def export_schema(output: Path) -> None:
    schema = TenantConfigDocument.model_json_schema()
    output.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


def apply(path: Path, database_url: str | None = None) -> dict[str, str]:
    """Reconcile tenant registry rows from a validated document."""
    from tenant_operator.provision import reconcile_tenant

    doc = load_document(path)
    return reconcile_tenant(doc, database_url=database_url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="School analytics tenant operator")
    sub = parser.add_subparsers(dest="command", required=True)

    validate_parser = sub.add_parser("validate", help="Validate a tenant YAML file")
    validate_parser.add_argument("--config", required=True, type=Path)

    schema_parser = sub.add_parser("schema", help="Write JSON Schema to stdout or file")
    schema_parser.add_argument("--output", type=Path, default=None)

    apply_parser = sub.add_parser("apply", help="Apply tenant config to the database")
    apply_parser.add_argument("--config", required=True, type=Path)
    apply_parser.add_argument("--database-url", default=None)

    args = parser.parse_args(argv)

    if args.command == "validate":
        errors = validate(args.config)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print(f"{args.config}: valid")
        return 0

    if args.command == "schema":
        if args.output:
            export_schema(args.output)
            print(f"Wrote {args.output}")
        else:
            print(json.dumps(TenantConfigDocument.model_json_schema(), indent=2))
        return 0

    if args.command == "apply":
        outcome = apply(args.config, database_url=args.database_url)
        print(json.dumps(outcome, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
