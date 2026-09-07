# Webcomic RSS feeds

This project scrapes configured webcomic pages every six hours and publishes RSS
feeds with GitHub Actions and GitHub Pages. Feed entries contain a link and the
comic image; the images remain hosted by the original comic site.

## Configure comics

Edit `comics.yml`. See `comics.example.yml` for the supported fields and a full
example. CSS selectors can be found with a browser's Inspect Element command.
XKCD is preconfigured using its JSON metadata endpoints, which provide reliable
titles, dates, image URLs, and hover text.

Two common layouts are supported:

- **Archive with full images:** select each archive entry and set
  `follow_item_links: false`.
- **Archive with links only:** select each archive entry, set
  `follow_item_links: true`, and make `image_selector`, `title_selector`, and
  `date_selector` match the individual comic page.

Test locally with:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pytest
pytest -q
python scripts/generate_feeds.py
```

Open `dist/index.html` to inspect the generated feed list.

## Publish

1. Create a GitHub repository and push these files to its `main` branch.
2. In **Settings → Pages → Build and deployment**, select **GitHub Actions** as
   the source.
3. Run **Update and publish comic feeds** from the Actions tab, or wait for the
   next scheduled run.
4. The workflow's deployment job shows the Pages URL. Subscribe to
   `<Pages URL>/<comic-slug>.xml` in your RSS reader.

Scheduled GitHub Actions use UTC. The current schedule runs at minute 17 every
sixth hour to avoid the busiest top-of-hour window.

Please keep request volume modest and respect each site's terms, robots policy,
and creators. If a comic already provides an official RSS feed, prefer it.
