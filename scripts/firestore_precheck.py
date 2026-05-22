"""Firestore migration precheck tool.

This script compares source and target Firestore projects without writing data.
It reports collection/document counts and doc ID differences recursively.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

from google.cloud import firestore
from google.oauth2 import service_account


@dataclass
class CollectionReport:
    """Aggregated report for a collection path."""

    path: str
    source_docs: int = 0
    target_docs: int = 0
    source_only_doc_ids: List[str] = field(default_factory=list)
    target_only_doc_ids: List[str] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare source/target Firestore data before migration."
    )
    parser.add_argument("--source-project", required=True, help="Source GCP project ID")
    parser.add_argument("--target-project", required=True, help="Target GCP project ID")
    parser.add_argument(
        "--source-cred",
        required=True,
        help="Path to source service account JSON",
    )
    parser.add_argument(
        "--target-cred",
        required=True,
        help="Path to target service account JSON",
    )
    parser.add_argument(
        "--max-diff-ids",
        type=int,
        default=20,
        help="Maximum number of doc IDs shown per side in diff lists",
    )
    parser.add_argument(
        "--output",
        default="firestore_precheck_report.json",
        help="Output JSON report path",
    )
    return parser.parse_args()


def create_client(project_id: str, cred_path: str) -> firestore.Client:
    cred_file = Path(cred_path)
    if not cred_file.exists():
        raise FileNotFoundError(f"Credential file not found: {cred_file}")
    credentials = service_account.Credentials.from_service_account_file(str(cred_file))
    return firestore.Client(project=project_id, credentials=credentials)


def top_collection_paths(client: firestore.Client) -> List[str]:
    return sorted(col.id for col in client.collections())


def collection_doc_ids(client: firestore.Client, collection_path: str) -> List[str]:
    return sorted(doc.id for doc in client.collection(collection_path).stream())


def child_collection_paths(
    client: firestore.Client, collection_path: str, doc_ids: Iterable[str]
) -> List[str]:
    child_paths: Set[str] = set()
    for doc_id in doc_ids:
        doc_ref = client.collection(collection_path).document(doc_id)
        for subcol in doc_ref.collections():
            child_paths.add(f"{collection_path}/{doc_id}/{subcol.id}")
    return sorted(child_paths)


def compare_collection(
    src: firestore.Client,
    dst: firestore.Client,
    collection_path: str,
    max_diff_ids: int,
    reports: Dict[str, CollectionReport],
) -> None:
    source_ids = collection_doc_ids(src, collection_path)
    target_ids = collection_doc_ids(dst, collection_path)

    source_set = set(source_ids)
    target_set = set(target_ids)
    source_only = sorted(source_set - target_set)[:max_diff_ids]
    target_only = sorted(target_set - source_set)[:max_diff_ids]

    reports[collection_path] = CollectionReport(
        path=collection_path,
        source_docs=len(source_ids),
        target_docs=len(target_ids),
        source_only_doc_ids=source_only,
        target_only_doc_ids=target_only,
    )

    union_ids = sorted(source_set | target_set)
    source_children = child_collection_paths(src, collection_path, union_ids)
    target_children = child_collection_paths(dst, collection_path, union_ids)
    child_union = sorted(set(source_children) | set(target_children))

    for child_path in child_union:
        compare_collection(src, dst, child_path, max_diff_ids, reports)


def build_report(
    src: firestore.Client, dst: firestore.Client, max_diff_ids: int
) -> Tuple[Dict[str, CollectionReport], Dict[str, int]]:
    reports: Dict[str, CollectionReport] = {}
    root_paths = sorted(set(top_collection_paths(src)) | set(top_collection_paths(dst)))
    for root_path in root_paths:
        compare_collection(src, dst, root_path, max_diff_ids, reports)

    summary = {
        "collections_checked": len(reports),
        "collections_with_count_diff": sum(
            1 for r in reports.values() if r.source_docs != r.target_docs
        ),
        "collections_with_doc_id_diff": sum(
            1
            for r in reports.values()
            if r.source_only_doc_ids or r.target_only_doc_ids
        ),
        "source_total_docs": sum(r.source_docs for r in reports.values()),
        "target_total_docs": sum(r.target_docs for r in reports.values()),
    }
    return reports, summary


def main() -> None:
    args = parse_args()
    src = create_client(args.source_project, args.source_cred)
    dst = create_client(args.target_project, args.target_cred)

    reports, summary = build_report(src, dst, args.max_diff_ids)
    payload = {
        "source_project": args.source_project,
        "target_project": args.target_project,
        "summary": summary,
        "collections": [asdict(reports[path]) for path in sorted(reports.keys())],
    }

    output_path = Path(args.output)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== Firestore precheck complete ===")
    print(f"Source project: {args.source_project}")
    print(f"Target project: {args.target_project}")
    print(f"Collections checked: {summary['collections_checked']}")
    print(f"Count diffs: {summary['collections_with_count_diff']}")
    print(f"Doc ID diffs: {summary['collections_with_doc_id_diff']}")
    print(f"Report file: {output_path.resolve()}")


if __name__ == "__main__":
    main()
