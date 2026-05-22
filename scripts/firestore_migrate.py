"""Firestore migration script (source -> target).

Features:
- Copies all collections recursively, including subcollections.
- Uses same document IDs on target.
- Re-runnable: writing to same doc ID overwrites (upsert behavior).
- Logs progress and writes a JSON summary report.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Set

from google.cloud import firestore
from google.oauth2 import service_account


@dataclass
class MigrationStats:
    source_project: str
    target_project: str
    started_at: str
    finished_at: str
    collection_paths_scanned: int
    documents_read: int
    documents_written: int
    documents_failed: int
    failed_doc_paths: List[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate all Firestore collections from source to target."
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
        "--report",
        default="firestore_migration_report.json",
        help="Output report JSON path",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and log writes without writing to target",
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


def child_collection_paths(
    src: firestore.Client, collection_path: str, doc_ids: Iterable[str]
) -> List[str]:
    child_paths: Set[str] = set()
    for doc_id in doc_ids:
        doc_ref = src.collection(collection_path).document(doc_id)
        for subcol in doc_ref.collections():
            child_paths.add(f"{collection_path}/{doc_id}/{subcol.id}")
    return sorted(child_paths)


def migrate_collection_recursive(
    src: firestore.Client,
    dst: firestore.Client,
    collection_path: str,
    stats: Dict[str, int | List[str]],
    dry_run: bool,
) -> None:
    stats["collection_paths_scanned"] = int(stats["collection_paths_scanned"]) + 1
    logging.info("Scanning collection: %s", collection_path)

    docs = list(src.collection(collection_path).stream())
    if not docs:
        logging.info("No docs in collection: %s", collection_path)
        return

    doc_ids: List[str] = []
    for doc in docs:
        doc_ids.append(doc.id)
        stats["documents_read"] = int(stats["documents_read"]) + 1
        doc_path = f"{collection_path}/{doc.id}"
        data = doc.to_dict() or {}

        try:
            if dry_run:
                logging.info("[DRY-RUN] Would write doc: %s", doc_path)
            else:
                dst.collection(collection_path).document(doc.id).set(data)
                logging.info("Wrote doc: %s", doc_path)
            stats["documents_written"] = int(stats["documents_written"]) + 1
        except Exception as exc:  # noqa: BLE001
            logging.exception("Failed writing doc: %s", doc_path)
            stats["documents_failed"] = int(stats["documents_failed"]) + 1
            failed_doc_paths = stats["failed_doc_paths"]
            assert isinstance(failed_doc_paths, list)
            failed_doc_paths.append(f"{doc_path} ({exc})")

    for child_path in child_collection_paths(src, collection_path, doc_ids):
        migrate_collection_recursive(src, dst, child_path, stats, dry_run)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def main() -> None:
    args = parse_args()
    configure_logging(args.log_level)

    started_at = datetime.now(timezone.utc).isoformat()
    src = create_client(args.source_project, args.source_cred)
    dst = create_client(args.target_project, args.target_cred)

    stats: Dict[str, int | List[str]] = {
        "collection_paths_scanned": 0,
        "documents_read": 0,
        "documents_written": 0,
        "documents_failed": 0,
        "failed_doc_paths": [],
    }

    root_paths = top_collection_paths(src)
    logging.info("Root collections found: %s", ", ".join(root_paths) if root_paths else "(none)")
    for root_path in root_paths:
        migrate_collection_recursive(src, dst, root_path, stats, args.dry_run)

    finished_at = datetime.now(timezone.utc).isoformat()
    report = MigrationStats(
        source_project=args.source_project,
        target_project=args.target_project,
        started_at=started_at,
        finished_at=finished_at,
        collection_paths_scanned=int(stats["collection_paths_scanned"]),
        documents_read=int(stats["documents_read"]),
        documents_written=int(stats["documents_written"]),
        documents_failed=int(stats["documents_failed"]),
        failed_doc_paths=list(stats["failed_doc_paths"]),  # type: ignore[arg-type]
    )

    report_path = Path(args.report)
    report_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("=== Firestore migration complete ===")
    print(f"Source project: {args.source_project}")
    print(f"Target project: {args.target_project}")
    print(f"Collection paths scanned: {report.collection_paths_scanned}")
    print(f"Documents read: {report.documents_read}")
    print(f"Documents written: {report.documents_written}")
    print(f"Documents failed: {report.documents_failed}")
    print(f"Report file: {report_path.resolve()}")
    if args.dry_run:
        print("Mode: dry-run (no writes were performed)")


if __name__ == "__main__":
    main()
