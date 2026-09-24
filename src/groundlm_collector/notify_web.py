"""Small localhost-only web UI for previewing and sending OpenReview notices."""

from __future__ import annotations

import argparse
import csv
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from .auth import create_client, load_keychain_credential
from .cli import VENUE_IDS
from .notifications import group_papers_by_author, render_message
from .notifier import build_target_papers, send_notifications
from .openreview_source import discover_venue, fetch_submissions


DEFAULT_SUBJECT = "GroundLM 2026 workshop: please check your paper materials"
DEFAULT_REPO_URL = "https://github.com/groundlm/GroundLM26_Papers"
DEFAULT_TEMPLATE = """{greeting}

We are preparing the GroundLM 2026 workshop materials. Please check the
following paper(s) in the workshop repository:

{papers}

Please check the paper PDF, supplementary materials, metadata, and ACL
publication-check result. If you find a problem, please update the relevant
file in the repository or contact the workshop organizers:
{repo_url}

Thank you,
GroundLM 2026 Workshop Organizers
"""


def _records(client):
    records = []
    for venue_id in VENUE_IDS:
        schema = discover_venue(client, venue_id)
        records.extend(fetch_submissions(client, schema))
    return records


def _csv_rows(path: Path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _form(
    data: dict[str, str],
    *,
    paper_choices: list[tuple[str, str]] = (),
    result: str = "",
) -> str:
    def val(key, default=""):
        return escape(data.get(key, default), quote=True)

    selected_paper = data.get("paper", "")
    paper_options = "".join(
        f"<option value='{escape(number, quote=True)}' "
        f"{'selected' if number == selected_paper else ''}>"
        f"{escape(number)} — {escape(title)}</option>"
        for number, title in paper_choices
    )
    return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>GroundLM notification sender</title>
<style>body{{font:16px system-ui;max-width:900px;margin:2rem auto;padding:0 1rem}}
label{{display:block;margin-top:1rem;font-weight:600}} input,select,textarea{{width:100%;box-sizing:border-box;padding:.55rem;font:inherit}}
textarea{{min-height:260px}}button{{margin-top:1rem;padding:.65rem 1rem;font-weight:600}}
.warning{{background:#fff3cd;padding:1rem;border:1px solid #e0b400}} .result{{white-space:pre-wrap;background:#f5f5f5;padding:1rem}}
small{{color:#555}}</style></head><body>
<h1>GroundLM notification sender</h1>
<p>This page is local-only. OpenReview credentials are read from macOS Keychain.</p>
<form method='post'>
<label>Recipients</label><select name='mode'>
<option value='archival' {"selected" if data.get("mode","archival")=="archival" else ""}>Accept/Findings + archival</option>
<option value='non-archival' {"selected" if data.get("mode")=="non-archival" else ""}>Accept/Findings + non-archival</option>
<option value='paper' {"selected" if data.get("mode")=="paper" else ""}>One paper number</option></select>
<label>Paper (only for one-paper mode)</label><select name='paper'>
<option value=''>Select a paper</option>{paper_options}</select>
<label>Subject</label><input name='subject' value='{val("subject", DEFAULT_SUBJECT)}'>
<label>Repository URL</label><input name='repo_url' value='{val("repo_url", DEFAULT_REPO_URL)}'>
<label>CC</label><input name='cc' value='{val("cc")}' placeholder='your-openreview-email@example.com'>
<small>OpenReview has no native CC field; CC entries are added as additional recipients of each message.</small>
<label>OpenReview message invitation</label><input name='invitation' value='{val("invitation")}' placeholder='VenueID/-/Message'>
<small>Required for sending through the current OpenReview API. The invitation must authorize organizer messages.</small>
<label>OpenReview signature</label><input name='signature' value='{val("signature")}' placeholder='EMNLP/2026/Workshop/GroundLM'>
<small>Usually the workshop group ID that owns the Message invitation.</small>
<label>Message template</label><small>Available placeholders: {{greeting}}, {{papers}}, {{repo_url}}</small>
<textarea name='template'>{val("template", DEFAULT_TEMPLATE)}</textarea>
<div class='warning'>Sending is irreversible. First use “Preview”. Only the separate confirmation button sends messages.</div>
<button name='action' value='preview'>Preview recipients and message</button>
<button name='action' value='send'>Send messages</button>
</form>{result}</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "GroundLMNotify/1.0"

    def do_GET(self):  # noqa: N802
        self._reply(_form({}, paper_choices=self._paper_choices()))

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        parsed = {key: values[-1] for key, values in parse_qs(raw).items()}
        try:
            result = self._run(parsed)
        except Exception as exc:  # show actionable errors in local UI
            result = f"<div class='result'>Error: {escape(str(exc))}</div>"
        self._reply(_form(parsed, paper_choices=self._paper_choices(), result=result))

    def _paper_choices(self):
        return [
            (row.get("number", ""), row.get("title", ""))
            for row in _csv_rows(self.server.csv_path)
            if row.get("number") and row.get("title")
        ]

    def _run(self, data):
        mode = data.get("mode", "archival")
        credential = load_keychain_credential()
        client = create_client(credential)
        records = _records(client)
        rows = _csv_rows(self.server.csv_path) if mode in {"paper", "archival"} else None
        papers = build_target_papers(records, mode, csv_rows=rows, paper_number=data.get("paper"))
        grouped = group_papers_by_author(papers)
        template = data.get("template", DEFAULT_TEMPLATE)
        subject = data.get("subject", DEFAULT_SUBJECT)
        repo_url = data.get("repo_url", DEFAULT_REPO_URL)
        cc = [item.strip() for item in data.get("cc", "").split(",") if item.strip()]
        invitation = data.get("invitation", "").strip()
        signature = data.get("signature", "").strip()
        preview = render_message(papers[:1], repo_url=repo_url, template=template)
        action = data.get("action")
        if action == "send":
            if not invitation:
                raise ValueError(
                    "An OpenReview message invitation is required for sending. "
                    "Use Preview first, then enter the organizer message invitation."
                )
            if "/Reviewers" in invitation or "/Area_Chairs" in invitation:
                raise ValueError(
                    "Do not use a Reviewers or Area_Chairs invitation. "
                    "Use the matching submission invitation ending in '/-/Message'."
                )
            if not signature:
                raise ValueError(
                    "An OpenReview signature is required for sending. "
                    "Use the workshop group ID, for example "
                    "EMNLP/2026/Workshop/GroundLM_Shared_Tasks."
                )
            result = send_notifications(
                client, papers, subject=subject, repo_url=repo_url,
                message_template=template, send=True, yes=True,
                cc=cc,
                invitation=invitation,
                signature=signature or None,
                log_path=self.server.log_path,
            )
            return f"<div class='result'>Sent {result.sent} message(s) to {result.planned} recipient(s).</div>"
        lines = [f"Preview: {len(papers)} paper(s), {len(grouped)} recipient(s)", "", preview]
        return f"<div class='result'>{escape(chr(10).join(lines))}</div>"

    def _reply(self, body):
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        return


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--csv", type=Path, default=Path("papers.csv"))
    parser.add_argument("--log", type=Path, default=Path("private/openreview_messages.jsonl"))
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.csv_path = args.csv
    server.log_path = args.log
    print(f"Open http://{args.host}:{args.port} in your browser")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
