"""
CLI tool to import documents from an NDJSON file into a Discovery Engine datastore.

Usage:
    python scripts/import_documents.py \
        --project your-project \
        --datastore your-datastore-id \
        --location global \
        --ndjson-file scripts/sample_datastore.ndjson

Prerequisites:
    - Application Default Credentials configured (gcloud auth application-default login)
    - Discovery Engine API enabled
    - The datastore must exist and have ACL enabled
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from google.cloud import discoveryengine_v1 as discoveryengine
from google.protobuf import struct_pb2


def parse_args():
    p = argparse.ArgumentParser(description="Import NDJSON documents into Discovery Engine")
    p.add_argument("--project", required=True)
    p.add_argument("--datastore", required=True)
    p.add_argument("--location", default="global")
    p.add_argument("--ndjson-file", required=True)
    p.add_argument("--branch", default="default_branch")
    return p.parse_args()


def build_acl_info(raw_acl: dict) -> discoveryengine.Document.AclInfo | None:
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


def main():
    args = parse_args()
    ndjson_path = Path(args.ndjson_file)
    if not ndjson_path.exists():
        print(f"File not found: {ndjson_path}", file=sys.stderr)
        sys.exit(1)

    client = discoveryengine.DocumentServiceClient()
    parent = (
        f"projects/{args.project}"
        f"/locations/{args.location}"
        f"/collections/default_collection"
        f"/dataStores/{args.datastore}"
        f"/branches/{args.branch}"
    )

    docs = []
    with ndjson_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            docs.append(json.loads(line))

    print(f"Importing {len(docs)} documents into {parent}")

    for raw in docs:
        doc_id = raw["id"]
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
            print(f"  ✓ {doc_id}: {result.name}")
        except Exception as exc:
            print(f"  ✗ {doc_id}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
