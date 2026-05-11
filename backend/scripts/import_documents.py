"""
CLI tool to import NDJSON documents into Discovery Engine datastores.

Supports two modes:
  1. Single datastore (--datastore + --ndjson-file)
  2. Registry-driven bulk import (--registry + --ndjson-dir)
     Reads datastore_registry.json and imports each matching .ndjson file
     from the directory.

     Expected directory layout (filenames are arbitrary; the script maps
     bucket type to the registry):
       ndjson/
         public.ndjson
         internal_dept_a.ndjson
         internal_dept_b.ndjson
         relate_ab.ndjson
         confidential.ndjson

Usage — single datastore:
    python scripts/import_documents.py \\
        --project my-project \\
        --datastore public-datastore \\
        --ndjson-file scripts/sample_public.ndjson

Usage — registry bulk import:
    python scripts/import_documents.py \\
        --project my-project \\
        --registry scripts/datastore_registry.json \\
        --ndjson-dir scripts/ \\
        --file-map public=sample_public.ndjson \\
                   internal.A=sample_internal_dept_a.ndjson \\
                   internal.B=sample_internal_dept_b.ndjson \\
                   relate.ab=sample_relate_ab.ndjson \\
                   confidential=sample_confidential.ndjson
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from google.cloud import discoveryengine_v1 as discoveryengine
from google.protobuf import struct_pb2


# ── Helpers ────────────────────────────────────────────────────────────────────

def build_acl_info(raw_acl: dict):
    if not raw_acl:
        return None
    readers = []
    for reader in raw_acl.get("readers", []):
        principals = []
        for p in reader.get("principals", []):
            if "userId" in p:
                principals.append(
                    discoveryengine.Document.AclInfo.AccessRestriction.Principal(
                        user_id=p["userId"]
                    )
                )
            elif "groupId" in p:
                principals.append(
                    discoveryengine.Document.AclInfo.AccessRestriction.Principal(
                        group_id=p["groupId"]
                    )
                )
        readers.append(
            discoveryengine.Document.AclInfo.AccessRestriction(principals=principals)
        )
    return discoveryengine.Document.AclInfo(readers=readers)


def dict_to_struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    for key, val in d.items():
        if val is None:
            continue
        if isinstance(val, str):
            s.fields[key].string_value = val
        elif isinstance(val, bool):
            s.fields[key].bool_value = val
        elif isinstance(val, (int, float)):
            s.fields[key].number_value = float(val)
    return s


def import_ndjson(
    client: discoveryengine.DocumentServiceClient,
    parent: str,
    ndjson_path: Path,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Import documents from an NDJSON file. Returns (success_count, error_count)."""
    success = 0
    errors = 0

    with ndjson_path.open() as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"  ✗ Line {lineno}: JSON parse error — {exc}", file=sys.stderr)
                errors += 1
                continue

            doc_id = raw.get("id")
            if not doc_id:
                print(f"  ✗ Line {lineno}: missing 'id' field", file=sys.stderr)
                errors += 1
                continue

            if dry_run:
                print(f"  [dry-run] {doc_id}")
                success += 1
                continue

            struct_data = dict_to_struct(raw.get("structData", {}))
            acl_info = build_acl_info(raw.get("acl_info", {}))
            content_raw = raw.get("content", {})
            content = (
                discoveryengine.Document.Content(
                    mime_type=content_raw.get("mimeType", "text/html"),
                    uri=content_raw.get("uri", ""),
                )
                if content_raw
                else None
            )

            document = discoveryengine.Document(
                id=doc_id,
                struct_data=struct_data,
                content=content,
            )
            if acl_info:
                document.acl_info = acl_info

            try:
                result = client.create_document(
                    request=discoveryengine.CreateDocumentRequest(
                        parent=parent,
                        document=document,
                        document_id=doc_id,
                    )
                )
                print(f"  ✓ {doc_id}")
                success += 1
            except Exception as exc:
                print(f"  ✗ {doc_id}: {exc}", file=sys.stderr)
                errors += 1

    return success, errors


def make_parent(project: str, location: str, datastore_id: str, branch: str) -> str:
    return (
        f"projects/{project}"
        f"/locations/{location}"
        f"/collections/default_collection"
        f"/dataStores/{datastore_id}"
        f"/branches/{branch}"
    )


# ── CLI ────────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Import NDJSON documents into Discovery Engine datastores"
    )
    p.add_argument("--project", required=True)
    p.add_argument("--location", default="global")
    p.add_argument("--branch", default="default_branch")
    p.add_argument("--dry-run", action="store_true")

    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--datastore", help="Single datastore ID")
    mode.add_argument("--registry", help="Path to datastore_registry.json")

    p.add_argument("--ndjson-file", help="Path to a single NDJSON file (single mode)")
    p.add_argument("--ndjson-dir", help="Directory containing NDJSON files (registry mode)")
    p.add_argument(
        "--file-map",
        nargs="+",
        metavar="BUCKET=FILE",
        help=(
            "Map registry bucket keys to filenames. "
            "Keys: public, internal.<DEPT>, relate.<ID>, confidential. "
            "Example: public=sample_public.ndjson internal.A=sample_internal_dept_a.ndjson"
        ),
    )
    return p.parse_args()


def main():
    args = parse_args()
    client = discoveryengine.DocumentServiceClient()

    if args.datastore:
        # ── Single datastore mode ──────────────────────────────────────────────
        if not args.ndjson_file:
            print("--ndjson-file is required with --datastore", file=sys.stderr)
            sys.exit(1)
        ndjson_path = Path(args.ndjson_file)
        if not ndjson_path.exists():
            print(f"File not found: {ndjson_path}", file=sys.stderr)
            sys.exit(1)
        parent = make_parent(args.project, args.location, args.datastore, args.branch)
        print(f"Importing {ndjson_path.name} → {parent}")
        ok, err = import_ndjson(client, parent, ndjson_path, dry_run=args.dry_run)
        print(f"Done: {ok} imported, {err} errors")

    else:
        # ── Registry bulk mode ─────────────────────────────────────────────────
        registry_path = Path(args.registry)
        if not registry_path.exists():
            print(f"Registry not found: {registry_path}", file=sys.stderr)
            sys.exit(1)
        with registry_path.open() as fh:
            registry = json.load(fh)

        if not args.file_map:
            print("--file-map is required with --registry", file=sys.stderr)
            sys.exit(1)

        ndjson_dir = Path(args.ndjson_dir) if args.ndjson_dir else registry_path.parent
        file_map = dict(pair.split("=", 1) for pair in args.file_map)

        total_ok = total_err = 0
        for bucket_key, filename in file_map.items():
            # Resolve datastore ID from registry
            parts = bucket_key.split(".", 1)
            if parts[0] == "public":
                ds_id = registry["public"]
            elif parts[0] == "internal" and len(parts) == 2:
                ds_id = registry.get("internal", {}).get(parts[1].upper())
            elif parts[0] == "relate" and len(parts) == 2:
                ds_id = registry.get("relate", {}).get(parts[1].lower())
            elif parts[0] == "confidential":
                ds_id = registry.get("confidential")
            else:
                print(f"  Unknown bucket key: {bucket_key}", file=sys.stderr)
                continue

            if not ds_id:
                print(f"  No datastore found for bucket '{bucket_key}' in registry", file=sys.stderr)
                continue

            ndjson_path = ndjson_dir / filename
            if not ndjson_path.exists():
                print(f"  File not found: {ndjson_path}", file=sys.stderr)
                continue

            parent = make_parent(args.project, args.location, ds_id, args.branch)
            print(f"\n[{bucket_key}] → datastore={ds_id}")
            print(f"  Importing {ndjson_path.name} → {parent}")
            ok, err = import_ndjson(client, parent, ndjson_path, dry_run=args.dry_run)
            print(f"  Done: {ok} imported, {err} errors")
            total_ok += ok
            total_err += err

        print(f"\nTotal: {total_ok} imported, {total_err} errors")


if __name__ == "__main__":
    main()
