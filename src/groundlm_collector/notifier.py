"""Build and send organizer notices through the OpenReview message API."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .models import RawSubmission
from .notifications import (
    NotificationPaper,
    group_papers_by_author,
    matches_mode,
    render_message,
)


NotificationTarget = str


@dataclass(frozen=True, slots=True)
class NotificationSendResult:
    planned: int
    sent: int


def build_target_papers(
    records: Sequence[RawSubmission],
    mode: str,
    *,
    csv_rows: Sequence[Mapping[str, str]] | None = None,
    paper_number: str | None = None,
) -> list[NotificationPaper]:
    if mode == "paper":
        if not paper_number or csv_rows is None:
            raise ValueError("paper mode requires --paper and papers.csv")
        row = next(
            (item for item in csv_rows if item.get("number") == paper_number), None
        )
        if row is None:
            raise ValueError(f"Paper number {paper_number!r} is not in papers.csv")
        forum = _forum_from_url(row.get("openreview_url", ""))
        matching = [record for record in records if record.forum == forum]
        if not matching:
            raise ValueError(f"OpenReview submission {forum!r} was not found")
        return [_notification_paper(matching[0], paper_number)]

    if mode == "archival":
        if csv_rows is None:
            raise ValueError("archival mode requires papers.csv")
        current = {
            _forum_from_url(row.get("openreview_url", "")): row["number"]
            for row in csv_rows
        }
        found = {
            record.forum
            for record in records
            if record.forum in current and matches_mode(record, "archival")
        }
        missing = sorted(set(current) - found)
        if missing:
            raise ValueError(
                "OpenReview records missing from archival list: "
                + ", ".join(missing)
            )
        return [
            _notification_paper(record, current[record.forum])
            for record in records
            if record.forum in current and matches_mode(record, "archival")
        ]

    if mode == "non-archival":
        return [
            _notification_paper(record, _display_number(record))
            for record in records
            if matches_mode(record, "non-archival")
        ]

    raise ValueError(f"Unknown notification mode: {mode!r}")


def send_notifications(
    client,
    papers: Sequence[NotificationPaper],
    *,
    subject: str,
    repo_url: str,
    send: bool = False,
    yes: bool = False,
    invitation: str | None = None,
    signature: str | None = None,
    reply_to: str | None = None,
    message_template: str | None = None,
    cc: Sequence[str] = (),
    log_path: Path | None = None,
) -> NotificationSendResult:
    grouped = group_papers_by_author(papers)
    if send and not yes:
        raise ValueError("Actual sending requires --yes confirmation")

    if not send:
        return NotificationSendResult(planned=len(grouped), sent=0)

    sent = 0
    cc_recipients = tuple(dict.fromkeys(item.strip() for item in cc if item.strip()))
    for recipient, author_papers in grouped.items():
        message = render_message(
            author_papers, repo_url=repo_url, template=message_template
        )
        if invitation:
            response = client.post_message(
                subject,
                list(dict.fromkeys([recipient, *cc_recipients])),
                message,
                invitation=invitation,
                signature=signature,
                replyTo=reply_to,
            )
        else:
            response = client.post_direct_message(
                subject, list(dict.fromkeys([recipient, *cc_recipients])), message
            )
        sent += 1
        if log_path:
            _append_log(log_path, recipient, author_papers, response)

    return NotificationSendResult(planned=len(grouped), sent=sent)


def _notification_paper(record: RawSubmission, number: str) -> NotificationPaper:
    return NotificationPaper(
        number=number,
        forum=record.forum,
        title=record.title,
        author_ids=record.author_ids,
    )


def _display_number(record: RawSubmission) -> str:
    venue = record.source_venue.rsplit("/", 1)[-1]
    return f"{venue}:{record.number}"


def _forum_from_url(url: str) -> str:
    parsed = urlparse(url)
    values = parse_qs(parsed.query).get("id", [])
    if values and values[0]:
        return values[0]
    raise ValueError(f"OpenReview URL has no forum id: {url!r}")


def _append_log(
    path: Path,
    recipient: str,
    papers: Sequence[NotificationPaper],
    response,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "sent_at_utc": datetime.now(timezone.utc).isoformat(),
        "recipient": recipient,
        "papers": [paper.number for paper in papers],
        "response": response,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
