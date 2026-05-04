# framer-scraper

A Python tool that scrapes a Framer website and saves all assets (HTML, CSS, JS, images, fonts, videos) so the site can be run offline or hosted independently. Strips Framer editor artifacts, badges, and analytics in the process.

## Requirements

- Python 3.8+
- pip

## Installation

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Install Playwright browsers

```bash
playwright install chromium
```

## Usage

### Scrape a Framer site

```bash
python framer_scraper.py <website_url>
```

**Examples:**

```bash
python framer_scraper.py https://meetsmeet.framer.website/
python framer_scraper.py mysite.framer.website
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `-o`, `--output` | `./scrap` | Base output directory |

Scraped files are saved to `./scrap/<domain>/`.

### Run the local server

After scraping, serve the site locally using the included server:

```bash
python serve.py
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

**Custom port:**

```bash
python serve.py 9000
```

**Verbose logging:**

```bash
VERBOSE=1 python serve.py
```

The server handles Framer's `?range=` byte-range requests that the JS client sends for chunked asset loading — a plain `python -m http.server` will not handle these correctly.

## Output structure

```
scrap/
└── <domain>/
    ├── index.html
    ├── work/
    │   └── index.html
    ├── assets/
    ├── css/
    ├── fonts/
    ├── images/
    ├── js/
    └── videos/
```

## What the scraper does

- Renders each page with a headless Chromium browser (Playwright) to capture fully hydrated HTML
- Scrolls the page to trigger lazy-loaded assets
- Downloads all external assets: stylesheets, scripts, images, fonts, videos
- Rewrites all URLs to local root-relative paths
- Crawls internal links to scrape multi-page sites
- Strips Framer-specific artifacts: editor bar, badge, analytics scripts, CMS iframes, tracking meta tags
- Patches JS modules to remove editor CDN imports and rewrite embedded CDN asset URLs
