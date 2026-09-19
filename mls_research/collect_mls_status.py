from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from mls_research.availability_pipeline import (
    TeamAvailabilitySnapshot,
    canonical_team,
    parse_datetime,
    parse_status_line,
    write_snapshots_jsonl,
)

DEFAULT_STATUS_URL = "https://www.mlssoccer.com/news/mlssoccer-com-injury-report"
USER_AGENT = "Appwiza MLS research/1.0 (+https://appwiza.com)"


def _extract_published_at(soup: BeautifulSoup) -> datetime | None:
    meta_candidates = [
        ("property", "article:published_time"),
        ("name", "date"),
        ("name", "pubdate"),
        ("itemprop", "datePublished"),
    ]
    for attr, value in meta_candidates:
        node = soup.find("meta", attrs={attr: value})
        if node and node.get("content"):
            try:
                return parse_datetime(node["content"])
            except Exception:
                pass

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or "")
        except Exception:
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            value = node.get("datePublished")
            if value:
                try:
                    return parse_datetime(value)
                except Exception:
                    pass
    return None


def _extract_matchday(soup: BeautifulSoup) -> int | None:
    title = ""
    if soup.title:
        title = soup.title.get_text(" ", strip=True)
    heading = soup.find(["h1", "h2"])
    if heading:
        title += " " + heading.get_text(" ", strip=True)
    match = re.search(r"matchday\s*(\d+)", title, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _clean_text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def parse_status_html(
    html: str,
    source_url: str,
    retrieved_at: datetime | None = None,
    observed_at_override: datetime | None = None,
) -> list[TeamAvailabilitySnapshot]:
    retrieved_at = retrieved_at or datetime.now(timezone.utc)
    soup = BeautifulSoup(html, "html.parser")
    published_at = _extract_published_at(soup)
    observed_at = observed_at_override or published_at or retrieved_at
    matchday = _extract_matchday(soup)
    sha = hashlib.sha256(html.encode("utf-8")).hexdigest()

    # A report is a complete team-level snapshot: "None" must be preserved as
    # an explicit empty team snapshot so old injuries do not carry forward.
    team_entries: dict[str, list] = {}
    current_team: str | None = None

    root = soup.find("article") or soup.find("main") or soup.body or soup
    for node in root.find_all(["h2", "h3", "h4", "h5", "p", "li"]):
        text = _clean_text(node)
        if not text:
            continue

        if node.name in {"h2", "h3", "h4", "h5"}:
            maybe_team = canonical_team(text)
            if maybe_team:
                current_team = maybe_team
                team_entries.setdefault(current_team, [])
            else:
                # Date and section headings do not terminate a team until the
                # next recognized team heading.
                continue
            continue

        if not current_team:
            continue

        if text.lower() in {"none", "none.", "no players listed"}:
            team_entries.setdefault(current_team, [])
            continue

        # Some CMS paragraphs contain multiple rows separated by hard breaks.
        raw_lines = [part.strip() for part in re.split(r"[\r\n]+", node.get_text("\n")) if part.strip()]
        for line in raw_lines:
            entry = parse_status_line(line)
            if entry:
                team_entries.setdefault(current_team, []).append(entry)

    snapshots = []
    for team, entries in sorted(team_entries.items()):
        snapshots.append(
            TeamAvailabilitySnapshot(
                team=team,
                observed_at=observed_at,
                source_url=source_url,
                entries=entries,
                published_at=published_at,
                retrieved_at=retrieved_at,
                matchday=matchday,
                source_sha256=sha,
            )
        )
    return snapshots


def collect_url(
    url: str,
    session: requests.Session,
    raw_dir: Path | None = None,
    observed_at_override: datetime | None = None,
) -> list[TeamAvailabilitySnapshot]:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    html = response.text
    retrieved_at = datetime.now(timezone.utc)

    if raw_dir:
        raw_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        stamp = retrieved_at.strftime("%Y%m%dT%H%M%SZ")
        (raw_dir / f"{stamp}_{digest}.html").write_text(html, encoding="utf-8")

    return parse_status_html(
        html,
        source_url=url,
        retrieved_at=retrieved_at,
        observed_at_override=observed_at_override,
    )


def _read_urls(path: str | None, direct_urls: list[str]) -> list[str]:
    urls = list(direct_urls)
    if path:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    if not urls:
        urls = [DEFAULT_STATUS_URL]

    deduped = []
    seen = set()
    for url in urls:
        if url not in seen:
            deduped.append(url)
            seen.add(url)
    return deduped


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect official MLS player status reports.")
    parser.add_argument("--url", action="append", default=[], help="MLS status-report URL; repeatable")
    parser.add_argument("--url-file", help="Text file with one official MLS status-report URL per line")
    parser.add_argument("--output", required=True, help="Append-only JSONL snapshot store")
    parser.add_argument("--raw-dir", help="Optional immutable raw HTML archive directory")
    parser.add_argument(
        "--observed-at",
        help="Override observation time for a source lacking publication metadata (ISO-8601 UTC preferred)",
    )
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    args = parser.parse_args()

    urls = _read_urls(args.url_file, args.url)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    override = parse_datetime(args.observed_at) if args.observed_at else None
    all_snapshots = []

    for index, url in enumerate(urls):
        snapshots = collect_url(
            url,
            session=session,
            raw_dir=Path(args.raw_dir) if args.raw_dir else None,
            observed_at_override=override,
        )
        if not snapshots:
            raise RuntimeError(f"No team snapshots parsed from {url}; refusing silent ingestion.")
        all_snapshots.extend(snapshots)
        print(f"{url}: parsed {len(snapshots)} team snapshots")
        if index + 1 < len(urls):
            time.sleep(max(0.0, args.delay_seconds))

    added = write_snapshots_jsonl(args.output, all_snapshots)
    print(f"Added {added} new immutable team snapshots to {args.output}")


if __name__ == "__main__":
    main()
