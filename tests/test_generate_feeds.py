from datetime import timezone
from pathlib import Path
from unittest.mock import Mock
from xml.etree import ElementTree as ET

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from generate_feeds import (
    build_rss,
    scrape_comic,
    scrape_penny_arcade,
    scrape_smbc,
    scrape_xkcd,
)


class FakeResponse:
    def __init__(self, text="", data=None):
        self.text = text
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_scrape_listing():
    session = Mock()
    session.get.return_value = FakeResponse("""
      <article><a class="link" href="/comic/42"><h2>Page 42</h2></a>
      <time datetime="2026-09-07T10:30:00-06:00"></time>
      <img class="comic" src="/images/42.png"></article>
    """)
    config = {
        "name": "Test Comic", "discovery_url": "https://example.test/archive",
        "item_selector": "article", "link_selector": "a.link",
        "title_selector": "h2", "date_selector": "time", "date_attribute": "datetime",
        "image_selector": "img.comic",
    }
    items = scrape_comic(config, session)
    assert len(items) == 1
    assert items[0].url == "https://example.test/comic/42"
    assert items[0].image_url == "https://example.test/images/42.png"
    assert items[0].published.tzinfo == timezone.utc


def test_rss_contains_image_and_self_link():
    config = {"name": "Test Comic", "discovery_url": "https://example.test"}
    listing_config = {
        **config, "item_selector": "article", "link_selector": "a",
        "image_selector": "img",
    }
    session = Mock()
    session.get.return_value = FakeResponse('<article><a href="/1">One</a><img src="/1.png"></article>')
    item = scrape_comic(listing_config, session)[0]
    xml = build_rss(config, [item], "https://feeds.test/test.xml")
    root = ET.fromstring(xml)
    assert root.findtext("channel/item/guid") == "https://example.test/1"
    assert "https://example.test/1.png" in root.findtext("channel/item/description")
    assert root.find("channel/{http://www.w3.org/2005/Atom}link").attrib["href"] == "https://feeds.test/test.xml"
    assert root.find("channel/item/enclosure") is None


def test_xkcd_feed_includes_hovertext_and_date():
    session = Mock()
    session.get.side_effect = [
        FakeResponse(data={
            "num": 2, "year": "2006", "month": "1", "day": "2",
            "safe_title": "Petit Trees", "img": "https://imgs.xkcd.com/comics/tree_cropped_(1).jpg",
            "alt": "'Petit' being a reference to Le Petit Prince.",
        }),
        FakeResponse(data={
            "num": 1, "year": "2006", "month": "1", "day": "1",
            "safe_title": "Barrel - Part 1", "img": "https://imgs.xkcd.com/comics/barrel_cropped_(1).jpg",
            "alt": "Don't we all.",
        }),
    ]
    config = {
        "latest_url": "https://xkcd.com/info.0.json",
        "item_url_template": "https://xkcd.com/{number}/info.0.json",
        "max_items": 2,
    }
    items = scrape_xkcd(config, session)
    xml = build_rss({"name": "xkcd", "discovery_url": "https://xkcd.com/"}, items, "")
    root = ET.fromstring(xml)
    description = root.findtext("channel/item/description")
    assert len(items) == 2
    assert items[0].title == "xkcd 2: Petit Trees"
    assert "Petit Prince" in description
    assert root.findtext("channel/item/pubDate") == "Mon, 02 Jan 2006 00:00:00 +0000"


def test_smbc_feed_includes_hovertext_and_bonus_panel():
    session = Mock()
    session.get.return_value = FakeResponse('''
          <title>Saturday Morning Breakfast Cereal - Test Comic</title>
          <div class="cc-newsheader"><a href="/comic/test-comic">Test Comic</a></div>
          <img id="cc-comic" src="/comics/123-20260907.png" title="Main hover text">
          <div id="aftercomic"><img src="/comics/bonus.png"></div>
          <a class="cc-prev" href="/comic/previous"></a>
        ''')
    config = {
        "homepage": "https://www.smbc-comics.com/",
        "discovery_url": "https://www.smbc-comics.com/",
        "max_items": 1,
    }
    items = scrape_smbc(config, session)
    xml = build_rss({"name": "SMBC", "homepage": config["homepage"]}, items, "")
    root = ET.fromstring(xml)
    description = root.findtext("channel/item/description")
    assert len(items) == 1
    assert items[0].title == "SMBC: Test Comic"
    assert items[0].url == "https://www.smbc-comics.com/comic/test-comic"
    assert "Main hover text" in description
    assert "Bonus panel:" in description
    assert "https://www.smbc-comics.com/comics/bonus.png" in description
    assert root.findtext("channel/item/pubDate") == "Mon, 07 Sep 2026 00:00:00 +0000"


def test_penny_arcade_combines_comic_image_and_blog_post():
    upstream_feed = '''<?xml version="1.0"?>
      <rss version="2.0"><channel>
        <item><title>A Comic</title><link>https://www.penny-arcade.com/comic/2026/09/07/a-comic</link>
          <description>New Comic: A Comic</description><pubDate>Mon, 07 Sep 2026 07:01:00 +0000</pubDate></item>
        <item><title>A Post</title><link>https://www.penny-arcade.com/news/post/2026/09/07/a-post</link>
          <description><![CDATA[<p>The complete blog body.</p>]]></description>
          <pubDate>Mon, 07 Sep 2026 19:23:00 +0000</pubDate></item>
      </channel></rss>'''
    comic_page = '<meta property="og:image" content="https://assets.penny-arcade.com/comic.jpg">'
    session = Mock()
    session.get.side_effect = [FakeResponse(upstream_feed), FakeResponse(comic_page)]
    config = {"feed_url": "https://www.penny-arcade.com/feed", "max_items": 10}

    items = scrape_penny_arcade(config, session)
    xml = build_rss(
        {"name": "Penny Arcade", "homepage": "https://www.penny-arcade.com/"},
        items,
        "",
    )
    root = ET.fromstring(xml)
    output_items = root.findall("channel/item")
    assert [item.findtext("title") for item in output_items] == ["[Comic] A Comic", "[Blog] A Post"]
    assert "https://assets.penny-arcade.com/comic.jpg" in output_items[0].findtext("description")
    assert "The complete blog body." in output_items[1].findtext("description")
