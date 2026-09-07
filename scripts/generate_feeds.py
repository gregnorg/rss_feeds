#!/usr/bin/env python3
"""Create static RSS feeds for selector-configured webcomics."""

from __future__ import annotations

import argparse
import html
import mimetypes
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import requests
import yaml
from bs4 import BeautifulSoup, Tag


USER_AGENT = "personal-webcomic-rss/1.0 (+GitHub Actions)"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
ATOM_NS = "http://www.w3.org/2005/Atom"
ET.register_namespace("content", CONTENT_NS)
ET.register_namespace("atom", ATOM_NS)


@dataclass(frozen=True)
class ComicItem:
    title: str
    url: str
    image_url: str
    published: datetime | None = None
    hovertext: str = ""


def get_text(root: Tag, selector: str | None, fallback: str = "") -> str:
    if not selector:
        return fallback
    node = root.select_one(selector)
    return node.get_text(" ", strip=True) if node else fallback


def get_url(root: Tag, selector: str, attribute: str, base_url: str) -> str:
    node = root.select_one(selector)
    if not node:
        return ""
    value = node.get(attribute)
    return urljoin(base_url, str(value)) if value else ""


def parse_date(value: str, date_format: str | None = None) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, date_format) if date_format else datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def extract_date(root: Tag, config: dict[str, Any]) -> datetime | None:
    selector = config.get("date_selector")
    if not selector:
        return None
    node = root.select_one(selector)
    if not node:
        return None
    attribute = config.get("date_attribute")
    value = str(node.get(attribute, "")) if attribute else node.get_text(" ", strip=True)
    return parse_date(value, config.get("date_format"))


def fetch_soup(session: requests.Session, url: str) -> BeautifulSoup:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def scrape_comic(config: dict[str, Any], session: requests.Session) -> list[ComicItem]:
    discovery_url = config["discovery_url"]
    soup = fetch_soup(session, discovery_url)
    roots = soup.select(config["item_selector"])
    max_items = int(config.get("max_items", 30))
    results: list[ComicItem] = []
    seen: set[str] = set()

    for root in roots[:max_items]:
        link_url = get_url(root, config["link_selector"], config.get("link_attribute", "href"), discovery_url)
        if not link_url or link_url in seen:
            continue
        seen.add(link_url)

        content_root = root
        content_base = discovery_url
        if config.get("follow_item_links", False):
            content_root = fetch_soup(session, link_url)
            content_base = link_url

        image_url = get_url(content_root, config["image_selector"], config.get("image_attribute", "src"), content_base)
        if not image_url:
            print(f"warning: no image found for {link_url}", file=sys.stderr)
            continue

        title = get_text(content_root, config.get("title_selector")) or get_text(root, config.get("title_selector"))
        if not title:
            title = config["name"]
        published = extract_date(content_root, config) or extract_date(root, config)
        results.append(ComicItem(title=title, url=link_url, image_url=image_url, published=published))

    results.sort(key=lambda item: item.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return results


def xkcd_item(data: dict[str, Any]) -> ComicItem:
    number = int(data["num"])
    published = datetime(
        int(data["year"]), int(data["month"]), int(data["day"]), tzinfo=timezone.utc
    )
    return ComicItem(
        title=f"xkcd {number}: {data['safe_title']}",
        url=f"https://xkcd.com/{number}/",
        image_url=data["img"],
        published=published,
        hovertext=data.get("alt", ""),
    )


def scrape_xkcd(config: dict[str, Any], session: requests.Session) -> list[ComicItem]:
    latest_response = session.get(config["latest_url"], timeout=30)
    latest_response.raise_for_status()
    latest_data = latest_response.json()
    latest_number = int(latest_data["num"])
    max_items = int(config.get("max_items", 10))
    results = [xkcd_item(latest_data)]

    number = latest_number - 1
    # A small allowance lets the scraper skip intentionally missing comic IDs.
    attempts_remaining = max_items + 20
    while len(results) < max_items and number > 0 and attempts_remaining > 0:
        url = config["item_url_template"].format(number=number)
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            results.append(xkcd_item(response.json()))
        except (requests.RequestException, KeyError, TypeError, ValueError) as error:
            print(f"warning: could not read {url}: {error}", file=sys.stderr)
        number -= 1
        attempts_remaining -= 1

    return results


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not slug or slug != value:
        raise ValueError(f"invalid slug {value!r}; use lowercase letters, numbers, and hyphens")
    return slug


def build_rss(config: dict[str, Any], items: list[ComicItem], feed_url: str) -> bytes:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = config["name"]
    ET.SubElement(channel, "link").text = config.get("homepage") or config["discovery_url"]
    ET.SubElement(channel, "description").text = config.get("description", f"Unofficial feed for {config['name']}")
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    if feed_url:
        ET.SubElement(channel, f"{{{ATOM_NS}}}link", {"href": feed_url, "rel": "self", "type": "application/rss+xml"})

    for comic_item in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = comic_item.title
        ET.SubElement(item, "link").text = comic_item.url
        ET.SubElement(item, "guid", {"isPermaLink": "true"}).text = comic_item.url
        if comic_item.published:
            ET.SubElement(item, "pubDate").text = format_datetime(comic_item.published)
        image_html = (
            f'<p><a href="{html.escape(comic_item.url, quote=True)}">'
            f'<img src="{html.escape(comic_item.image_url, quote=True)}" '
            f'alt="{html.escape(comic_item.title, quote=True)}" '
            f'title="{html.escape(comic_item.hovertext, quote=True)}"></a></p>'
        )
        if comic_item.hovertext:
            image_html += f'<p>{html.escape(comic_item.hovertext)}</p>'
        ET.SubElement(item, "description").text = image_html
        ET.SubElement(item, f"{{{CONTENT_NS}}}encoded").text = image_html
        mime_type = mimetypes.guess_type(comic_item.image_url.split("?", 1)[0])[0] or "image/jpeg"
        ET.SubElement(item, "enclosure", {"url": comic_item.image_url, "length": "0", "type": mime_type})

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def build_index(site: dict[str, Any], feeds: list[tuple[str, str]]) -> str:
    links = "\n".join(
        f'<li><a href="{html.escape(slug)}.xml">{html.escape(name)}</a></li>' for slug, name in feeds
    ) or "<li>No comics are configured yet.</li>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{html.escape(site.get('title', 'Webcomic feeds'))}</title></head>
<body><main><h1>{html.escape(site.get('title', 'Webcomic feeds'))}</h1>
<p>{html.escape(site.get('description', 'Generated RSS feeds'))}</p><ul>{links}</ul></main></body></html>
"""


def generate(config_path: Path, output_dir: Path, base_url: str = "") -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    site = config.get("site", {})
    comics = config.get("comics", [])
    if not isinstance(comics, list):
        raise ValueError("comics must be a list")

    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    feeds: list[tuple[str, str]] = []

    for comic in comics:
        slug = safe_slug(comic["slug"])
        print(f"scraping {comic['name']}...")
        items = scrape_xkcd(comic, session) if comic.get("source") == "xkcd" else scrape_comic(comic, session)
        feed_url = f"{base_url.rstrip('/')}/{slug}.xml" if base_url else ""
        (output_dir / f"{slug}.xml").write_bytes(build_rss(comic, items, feed_url))
        feeds.append((slug, comic["name"]))
        print(f"wrote {len(items)} items to {slug}.xml")

    (output_dir / "index.html").write_text(build_index(site, feeds), encoding="utf-8")
    (output_dir / ".nojekyll").touch()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("comics.yml"))
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()
    generate(args.config, args.output, args.base_url)


if __name__ == "__main__":
    main()
