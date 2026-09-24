"""CLI for previewing and sending GroundLM OpenReview author notices."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from collections.abc import Sequence

from .auth import create_client, load_keychain_credential
from .cli import VENUE_IDS
from .notifications import group_papers_by_author
from .notifier import build_target_papers, send_notifications
from .openreview_source import discover_venue, fetch_submissions


DEFAULT_SUBJECT = "GroundLM 2026 workshop: please check your paper materials"
DEFAULT_REPO_URL = "https://github.com/groundlm/GroundLM26_Papers"


def discover_records(client) -> list:
    records = []
    for venue_id in VENUE_IDS:
        schema = discover_venue(client, venue_id)
        records.extend(fetch_submissions(client, schema))
    return records


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--paper", metavar="NUMBER", help="papers.csv number")
    target.add_argument(
        "--archival",
        action="store_true",
        help="Accept/Findings archival papers in the current papers.csv list",
    )
    target.add_argument(
        "--non-archival",
        action="store_true",
        help="Accept/Findings non-archival submissions",
    )
    parser.add_argument("--csv", type=Path, default=Path("papers.csv"))
    parser.add_argument("--subject", default=DEFAULT_SUBJECT)
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    parser.add_argument(
        "--invitation",
        help="Use OpenReview post_message with this message invitation",
    )
    parser.add_argument(
        "--signature",
        help="OpenReview group ID signing the message, usually the workshop group",
    )
    parser.add_argument("--reply-to")
    parser.add_argument(
        "--cc",
        action="append",
        default=[],
        help="Additional OpenReview-recognized recipient; may be repeated",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=Path("private/openreview_messages.jsonl"),
        help="Local ignored send log",
    )
    parser.add_argument(
        "--send", action="store_true", help="Actually send through OpenReview"
    )
    parser.add_argument(
        "--yes", action="store_true", help="Confirm actual sending"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.send and not args.yes:
        raise SystemExit("--send requires --yes")

    credential = load_keychain_credential()
    client = create_client(credential)
    records = discover_records(client)
    csv_rows = _read_csv_rows(args.csv) if (args.paper or args.archival) else None
    if args.paper:
        mode = "paper"
    elif args.archival:
        mode = "archival"
    else:
        mode = "non-archival"
    papers = build_target_papers(
        records,
        mode,
        csv_rows=csv_rows,
        paper_number=args.paper,
    )
    grouped = group_papers_by_author(papers)
    action = "sending" if args.send else "dry-run"
    print(
        f"mode={mode} papers={len(papers)} recipients={len(grouped)} action={action}",
        flush=True,
    )
    for recipient, recipient_papers in grouped.items():
        numbers = ", ".join(paper.number for paper in recipient_papers)
        print(f"{recipient}: {numbers}")

    result = send_notifications(
        client,
        papers,
        subject=args.subject,
        repo_url=args.repo_url,
        send=args.send,
        yes=args.yes,
        invitation=args.invitation,
        signature=args.signature,
        reply_to=args.reply_to,
        cc=args.cc,
        log_path=args.log,
    )
    print(f"planned={result.planned} sent={result.sent}", flush=True)
    return 0


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


if __name__ == "__main__":
    raise SystemExit(main())
