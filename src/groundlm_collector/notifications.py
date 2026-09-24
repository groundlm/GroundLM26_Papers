"""Pure recipient selection and message rendering for OpenReview notices."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .models import RawSubmission
from .selection import SHARED_TASK_VENUE


NotificationMode = Literal["archival", "non-archival"]


@dataclass(frozen=True, slots=True)
class NotificationPaper:
    number: str
    forum: str
    title: str
    author_ids: tuple[str, ...]


def matches_mode(raw: RawSubmission, mode: NotificationMode) -> bool:
    decision = (raw.decision or "").strip().casefold()
    if decision not in {"accept", "findings"}:
        return False

    if mode == "archival":
        return raw.source_venue == SHARED_TASK_VENUE or (
            (raw.submission_type or "").strip().casefold().startswith("archival")
        )
    return (raw.submission_type or "").strip().casefold().startswith("non-archival")


def group_papers_by_author(
    papers: Iterable[NotificationPaper],
) -> dict[str, tuple[NotificationPaper, ...]]:
    grouped: OrderedDict[str, list[NotificationPaper]] = OrderedDict()
    canonical_keys: dict[str, str] = {}
    for paper in papers:
        for author_id in paper.author_ids:
            normalized = author_id.strip().casefold()
            if not normalized:
                continue
            key = canonical_keys.setdefault(normalized, author_id.strip())
            grouped.setdefault(key, []).append(paper)
    return {key: tuple(value) for key, value in grouped.items()}


def render_message(
    papers: Iterable[NotificationPaper],
    *,
    repo_url: str,
    greeting: str = "Dear author,",
    template: str | None = None,
) -> str:
    paper_lines = "\n".join(
        f"- {paper.number} — {paper.title}" for paper in papers
    )
    default_template = """{greeting}

We are preparing the GroundLM 2026 workshop materials. Please check the
following paper(s) in the workshop repository:

    {papers}

Please check the paper PDF, supplementary materials, metadata, and ACL
publication-check result. If you find a problem, please update the relevant
file in the repository or contact the workshop organizers if you need access:
{repo_url}

Thank you,
GroundLM 2026 Workshop Organizers
"""
    selected = template if template is not None else default_template
    # Replace only our documented placeholders.  Using ``str.format`` here
    # would interpret BibTeX/LaTeX braces in an author's custom message.
    return (
        selected.replace("{greeting}", greeting)
        .replace("{papers}", paper_lines)
        .replace("{repo_url}", repo_url)
    )
