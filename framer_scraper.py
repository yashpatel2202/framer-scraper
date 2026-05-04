#!/usr/bin/env python3
"""
Framer Site Scraper
==================
Scrapes a Framer website and saves all components, HTML, CSS, JS,
images, fonts, and videos so it can be run offline or hosted independently.

Usage:
    python framer_scraper.py <website_url>

Output:
    ./scrap/<domain>/
"""

import argparse
import hashlib
import mimetypes
import os
import re
import sys
import time
from typing import Optional
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("❌ playwright not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ beautifulsoup4 not installed. Run: pip install beautifulsoup4")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("❌ requests not installed. Run: pip install requests")
    sys.exit(1)


# ── Framer-specific CDN domains ──────────────────────────────────────────────
FRAMER_DOMAINS = [
    "framerusercontent.com",
    "framer.com",
    "framer.media",
    "fonts.gstatic.com",
    "fonts.googleapis.com",
    "events.framer.com",
]

# ── URL patterns to skip (CMS, editor, analytics, badge) ────────────────────
FRAMER_SKIP_PATTERNS = [
    "events.framer.com",
    "analytics",
    "gtag",
    "google-analytics",
    "/edit/",
    "edit.",
    "editorbar",
    "editor-bar",
    "__framer_badge",
    "framer-badge",
    "access-token",
    "bootstrap.",       # Framer editor bootstrap (not Bootstrap CSS)
    "init.mjs",
    # Framer editor module URLs (framer.com/m/ = Framer package CDN)
    "framer.com/m/bootstrap",
    "framer.com/m/editor",
    "framer.com/m/toolbar",
    "framer.com/m/init",
    "framer.com/m/site-publisher",
    "framer.com/m/framer-editor",
]

# ── JS content patterns that identify editor scripts (checked on script content/src) ──
EDITOR_SCRIPT_PATTERNS = [
    "events.framer.com", "analytics", "gtag", "google-analytics",
    "editorbar", "editor-bar", "__framer_badge", "framer-badge",
    "framer.com/m/bootstrap", "framer.com/m/editor", "framer.com/m/init",
    "edit/init",
    "bootstrap.", "access-token", "framerInternalRepresentation",
    "data-framer-appear-id",
]


def sanitize_filename(url: str) -> str:
    """Convert a URL into a safe local filename preserving extension."""
    parsed = urlparse(url)
    path = unquote(parsed.path).strip("/")
    if not path:
        path = "index"
    # Keep directory structure but sanitize
    path = re.sub(r'[<>:"|?*]', '_', path)
    # Add query hash if present to avoid collisions
    if parsed.query:
        qhash = hashlib.md5(parsed.query.encode()).hexdigest()[:8]
        base, ext = os.path.splitext(path)
        path = f"{base}_{qhash}{ext}"
    return path


def guess_extension(url: str, content_type: str = "") -> str:
    """Guess file extension from URL or content-type."""
    parsed = urlparse(url)
    _, ext = os.path.splitext(parsed.path)
    if ext and len(ext) < 8:
        return ext
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return guessed
    return ""


def classify_asset(url: str) -> str:
    """Classify asset into subfolder based on URL/extension."""
    lower = url.lower()
    path = urlparse(lower).path
    if any(path.endswith(e) for e in ['.css']):
        return "css"
    if any(path.endswith(e) for e in ['.js', '.mjs']):
        return "js"
    if any(path.endswith(e) for e in ['.woff', '.woff2', '.ttf', '.otf', '.eot']):
        return "fonts"
    if any(path.endswith(e) for e in ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.avif', '.ico']):
        return "images"
    if any(path.endswith(e) for e in ['.mp4', '.webm', '.ogg', '.mov']):
        return "videos"
    if any(path.endswith(e) for e in ['.mp3', '.wav']):
        return "audio"
    # Check content patterns
    if "font" in lower:
        return "fonts"
    if any(x in lower for x in ["image", "img", "photo", "picture"]):
        return "images"
    return "assets"


class FramerScraper:
    def __init__(self, url: str, output_base: str = "./scrap"):
        self.url = url.rstrip("/")
        if not self.url.startswith("http"):
            self.url = "https://" + self.url
        self.parsed_url = urlparse(self.url)
        self.domain = self.parsed_url.netloc.replace(":", "_")
        self.output_dir = Path(output_base) / self.domain
        self.downloaded: dict[str, str] = {}       # url -> root-relative path (/images/x.png)
        self._downloaded_disk: dict[str, str] = {}  # url -> disk-relative path (images/x.png)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": self.url + "/",
            "Origin": self.url,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "font",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        })
        self.network_resources: list[dict] = []
        self.visited_pages: set[str] = set()
        self.pages_to_visit: list[str] = [self.url]
        self.is_multipage = True  # Can be toggled via CLI if needed

    def run(self):
        """Main entry point for multi-page scraping."""
        print(f"\n🔍 Scraping site: {self.url}")
        print(f"📁 Output:        {self.output_dir.resolve()}\n")

        self.output_dir.mkdir(parents=True, exist_ok=True)

        while self.pages_to_visit:
            current_url = self.pages_to_visit.pop(0)
            if current_url in self.visited_pages:
                continue

            print(f"\n📄 Processing page: {current_url}")
            self.visited_pages.add(current_url)
            self._scrape_page(current_url)

        # Final pass: rewrite remaining remote URLs in all JS files now that
        # self.downloaded is fully populated (Google Fonts, modules, etc.)
        self._post_process_js_files()

        print(f"\n✅ Scraping complete!")
        print(f"📁 Saved to: {self.output_dir.resolve()}")
        print(f"📄 Total pages: {len(self.visited_pages)}")
        print(f"📦 Total assets: {len(self.downloaded)}")
        print(f"\n🌐 To preview: cd {self.output_dir.resolve()} && python -m http.server 8000")

    def _get_save_path(self, url: str) -> Path:
        """Map a URL to a local file path (e.g., /work -> work/index.html)."""
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        
        if not path:
            return self.output_dir / "index.html"
        
        # If it looks like a file (has extension), use it
        if "." in os.path.basename(path):
            return self.output_dir / path
            
        # Otherwise treat as directory and use index.html
        return self.output_dir / path / "index.html"

    def _scrape_page(self, url: str):
        """Render and process a single page."""
        # Step 1: Render page
        html_content = self._render_page(url)
        if not html_content:
            return

        # Step 2: Parse HTML
        soup = BeautifulSoup(html_content, "html.parser")

        # Step 3: Discover and download assets
        self._process_stylesheets(soup)
        self._process_scripts(soup)
        self._process_images(soup)
        self._process_videos(soup)
        self._process_fonts(soup)
        self._process_links(soup)
        self._process_inline_styles(soup)
        self._process_meta_tags(soup)
        self._download_network_resources()

        # Step 4: Discover internal links for crawling
        self._discover_links(soup, url)

        # Step 5: Rewrite remaining URLs in HTML
        final_html = self._rewrite_html(str(soup))

        # Step 6: Clean up Framer-specific stuff
        final_html = self._cleanup_framer(final_html)

        # Step 7: Save final HTML
        save_path = self._get_save_path(url)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(final_html, encoding="utf-8")
        print(f"  💾 Saved to {save_path.relative_to(self.output_dir)}")

    def _discover_links(self, soup: BeautifulSoup, current_url: str):
        """Find internal links and add to visit queue."""
        for a in soup.find_all("a", href=True):
            href = a["href"]
            # Resolve to absolute
            full_url = urljoin(current_url, href).split("#")[0].rstrip("/")
            
            parsed_full = urlparse(full_url)
            # Only same domain
            if parsed_full.netloc == self.parsed_url.netloc:
                if full_url not in self.visited_pages and full_url not in self.pages_to_visit:
                    # Skip common non-page extensions
                    if not any(full_url.endswith(ext) for ext in ['.pdf', '.zip', '.png', '.jpg']):
                        self.pages_to_visit.append(full_url)
                
                # Rewrite link to be relative/local
                local_save_path = self._get_save_path(full_url)
                rel_link = os.path.relpath(local_save_path, self._get_save_path(current_url).parent)
                a["href"] = rel_link

    def _render_page(self, url: str) -> Optional[str]:
        """Use Playwright to fully render the Framer page."""
        print(f"  🌐 Rendering {url}...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            # Capture network requests for assets
            def on_response(response):
                try:
                    url = response.url
                    ct = response.headers.get("content-type", "")
                    if response.ok and not url.startswith("data:"):
                        self.network_resources.append({
                            "url": url,
                            "content_type": ct,
                            "status": response.status,
                        })
                except Exception:
                    pass

            page.on("response", on_response)

            # Block Framer editor/badge CDN requests so they never load during rendering.
            # This keeps the captured HTML clean — no editor bar DOM artifacts → no hydration mismatch.
            def block_editor_request(route):
                route.abort()

            for pattern in [
                "**/framer.com/edit/**",
                "**/framer.com/m/bootstrap**",
                "**/framer.com/m/editor**",
                "**/framer.com/m/toolbar**",
                "**/framer.com/m/init**",
                "**/framer.com/m/site-publisher**",
                "**/events.framer.com/**",
            ]:
                page.route(pattern, block_editor_request)

            print(f"    📄 Loading...")
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
            except Exception as e:
                print(f"    ⚠️  Timeout/Error loading {url}: {e}")
                browser.close()
                return None

            # Scroll to trigger lazy loading
            page.evaluate("""
                async () => {
                    const delay = ms => new Promise(r => setTimeout(r, ms));
                    const height = document.body.scrollHeight;
                    const step = window.innerHeight;
                    for (let y = 0; y < height; y += step) {
                        window.scrollTo(0, y);
                        await delay(300);
                    }
                    window.scrollTo(0, 0);
                    await delay(500);
                }
            """)

            # Wait for any final network activity
            time.sleep(1)

            # Get full rendered HTML
            html = page.content()

            browser.close()

        return html

    def _download_asset(self, url: str, force_subfolder: str = "") -> Optional[str]:
        """Download an asset and return its local relative path."""
        if not url or url.startswith("data:") or url.startswith("javascript:"):
            return None

        # Skip Framer CMS, editor, and analytics URLs
        if any(pat in url.lower() for pat in FRAMER_SKIP_PATTERNS):
            print(f"  ⏭️  Skipped (Framer internal): {url[:80]}")
            return None

        # Resolve relative URLs
        full_url = urljoin(self.url + "/", url)

        if full_url in self.downloaded:
            return self.downloaded[full_url]

        try:
            resp = self.session.get(full_url, timeout=30)
            if resp.status_code != 200:
                print(f"  ⚠️  Failed ({resp.status_code}): {full_url[:80]}")
                return None
        except Exception as e:
            print(f"  ⚠️  Error: {full_url[:80]} - {e}")
            return None

        # Determine subfolder and filename
        subfolder = force_subfolder or classify_asset(full_url)
        filename = sanitize_filename(full_url)

        # Ensure extension
        if not os.path.splitext(filename)[1]:
            ext = guess_extension(full_url, resp.headers.get("content-type", ""))
            if ext:
                filename += ext

        # Avoid doubling: e.g. subfolder="images" + filename="images/HASH.png" → "images/HASH.png"
        if filename.startswith(subfolder + "/") or filename.startswith(subfolder + os.sep):
            local_path = Path(filename)
        else:
            local_path = Path(subfolder) / filename
        full_path = self.output_dir / local_path

        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(resp.content)

        # Store as BOTH: a disk-relative path (for CSS relpath calcs)
        # and a root-relative path (for HTML attributes) — use /path for HTML
        rel_path = str(local_path).replace("\\", "/")
        root_rel_path = "/" + rel_path
        self.downloaded[full_url] = root_rel_path   # root-relative for HTML/JS rewriting
        self._downloaded_disk[full_url] = rel_path  # disk-relative for CSS relpath calcs
        print(f"  ✅ {rel_path}")

        # For JS modules: patch editor imports, rewrite CDN asset URLs, chase dynamic imports.
        # Also handle versioned filenames like Robot.js@0.0.57 where suffix is ".57", not ".js".
        is_js = full_path.suffix in (".mjs", ".js") or bool(
            re.search(r'\.m?js', full_path.stem)  # catches Robot.js@0.0 -> stem has ".js"
        )
        if is_js:
            self._patch_editor_imports(full_path)
            self._patch_js_cdn_urls(full_path)
            self._chase_dynamic_imports(full_path, full_url)

        return root_rel_path

    def _patch_editor_imports(self, full_path: Path):
        """Replace Framer editor CDN dynamic imports with no-op stubs in a JS file."""
        try:
            text = full_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return

        # framer.com/edit/init.mjs exports {createEditorBar}, so stub must match that shape.
        # createEditorBar() is called and its return value becomes the module default export.
        # Returning ()=>null makes it a valid React component that renders nothing.
        stub_edit    = "Promise.resolve({createEditorBar:()=>(()=>null)})"
        stub_default = "Promise.resolve({default:()=>{}})"
        stub_empty   = "Promise.resolve({})"

        # Framer editor CDN dynamic imports (import(`https://framer.com/edit/...`))
        patched = re.sub(
            r"""import\(\s*(['"`])https://framer\.com/edit/[^'"`]*\1\s*\)""",
            stub_edit, text
        )
        # framer.com/m/bootstrap / editor / toolbar / init style imports
        patched = re.sub(
            r"""import\(\s*(['"`])https://framer\.com/m/(?:bootstrap|editor|toolbar|init|site-publisher|framer-editor)[^'"`]*\1\s*\)""",
            stub_default, patched
        )
        # Editor-only lazy-module properties: null them out entirely.
        # loadSnippetsModule/loadEditorModule use pe(()=>import('./hash.mjs')).
        # Framer runtime guards: if (!snippetsModule) return — null triggers that guard safely.
        patched = re.sub(
            r"""(loadSnippetsModule|loadEditorModule)\s*:\s*[^(]*\(\s*\(\s*\)\s*=>\s*import\(\s*[`'"][^`'"]*[`'"]\s*\)\s*\)""",
            lambda m: m.group(1) + ":null",
            patched
        )
        # Remove Framer badge hydrateRoot call — badge container is never in scraped HTML
        # so hydrateRoot(null, ...) throws React error #405 (invalid container).
        patched = re.sub(
            r"""\(function\(\)\{Pi&&p\(\(\)=>\{E\(document\.getElementById\(`__framer-badge-container`\).*?\)\}\)\}\)\(\)""",
            "",
            patched
        )
        # Stub framer.com/m/phosphor-icons and material-icons dynamic imports
        patched = re.sub(
            r"""import\(\s*(['"`])https://framer\.com/m/(?:phosphor-icons|material-icons)[^'"`]*\1\s*\)""",
            stub_empty, patched
        )
        # Remove Framer badge redirect URL (used in badge anchor href)
        patched = re.sub(
            r'https://www\.framer\.com/r/badge/[^\s"\'`]*',
            "", patched
        )
        # Replace screenshot.framer.invalid placeholder (used for canvas preview in Framer editor)
        patched = patched.replace("screenshot.framer.invalid", "about:blank")
        if patched != text:
            full_path.write_text(patched, encoding="utf-8")
            print(f"  🔧 Patched editor imports in {full_path.name}")

    def _patch_js_cdn_urls(self, full_path: Path):
        """Download remote asset URLs found in a JS file and rewrite them to local paths.
        Handles framerusercontent.com (images/videos/fonts/assets/modules),
        and Google Fonts (fonts.gstatic.com)."""
        try:
            text = full_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return

        # framerusercontent.com asset/module URLs — stop at ), ,, ; to avoid capturing trailing syntax
        cdn_urls = re.findall(
            r'https://framerusercontent\.com/(?:images|videos|fonts|assets|modules|third-party-assets)/[^\s"\'`\\),;]+',
            text
        )
        # Google Fonts woff2/woff/ttf URLs embedded as strings in the Framer JS bundle
        gstatic_urls = re.findall(
            r'https://fonts\.gstatic\.com/s/[^\s"\'`\\),;]+',
            text
        )

        all_urls = list(set(cdn_urls) | set(gstatic_urls))
        if not all_urls:
            return

        # Track which subfolder each base URL belongs to
        base_subfolder: dict[str, str] = {}
        for url in cdn_urls:
            base_subfolder[url.split("?")[0]] = ""          # let classify_asset pick
        for url in gstatic_urls:
            base_subfolder[url.split("?")[0]] = "fonts"     # always fonts for gstatic

        # Group query-string variants by base URL
        base_to_variants: dict[str, set[str]] = {}
        for url in all_urls:
            base = url.split("?")[0]
            base_to_variants.setdefault(base, set()).add(url)

        patched = text
        changed = False
        for base_url, variants in base_to_variants.items():
            subfolder = base_subfolder.get(base_url, "")
            if base_url not in self.downloaded:
                self._download_asset(base_url, subfolder)
            root_rel = self.downloaded.get(base_url)
            if not root_rel:
                continue
            for variant in variants:
                patched = patched.replace(variant, root_rel)
                changed = True

        if changed:
            full_path.write_text(patched, encoding="utf-8")
            print(f"  🖼️  Patched CDN asset URLs in {full_path.name}")

    def _chase_dynamic_imports(self, full_path: Path, original_url: str):
        """Scan a JS module for relative dynamic import() calls and download them.
        Also handles absolute-URL static re-exports (Framer icon-pack stubs)."""
        try:
            text = full_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return

        # ── Relative dynamic imports: import('./chunk.mjs') ─────────────────
        found = re.findall(r"""import\(\s*['"`]\./([^'"`]+\.m?js[^'"`]*)[`'"]\s*\)""", text)

        # Base URL is the directory of the original JS file
        base = original_url.rsplit("/", 1)[0] + "/"
        for rel in set(found):
            chunk_url = base + rel
            if chunk_url in self.downloaded:
                continue
            if any(pat in chunk_url.lower() for pat in FRAMER_SKIP_PATTERNS):
                continue
            result = self._download_asset(chunk_url)
            if result is None:
                filename = sanitize_filename(chunk_url)
                subfolder = classify_asset(chunk_url)
                stub_path = self.output_dir / subfolder / filename
                if not stub_path.exists():
                    stub_path.parent.mkdir(parents=True, exist_ok=True)
                    stub_path.write_text("export default {};", encoding="utf-8")
                    print(f"  📝 Created stub for missing chunk: {stub_path.name}")

        # ── Absolute static re-exports: export * from "https://..." ─────────
        # These appear in Framer icon-pack stubs (framer.com/m/phosphor-icons etc.)
        abs_exports = re.findall(
            r"""(?:export\s+(?:\*|\{[^}]*\})\s+from\s+|import\s+(?:\*|\{[^}]*\}|\w+)\s+from\s+)['"`](https://framerusercontent\.com/modules/[^'"`]+)['"`]""",
            text
        )
        patched = text
        changed = False
        for abs_url in set(abs_exports):
            if any(pat in abs_url.lower() for pat in FRAMER_SKIP_PATTERNS):
                continue
            if abs_url not in self.downloaded:
                self._download_asset(abs_url, "")
            root_rel = self.downloaded.get(abs_url)
            if root_rel:
                patched = patched.replace(f'"{abs_url}"', f'"{root_rel}"')
                patched = patched.replace(f"'{abs_url}'", f"'{root_rel}'")
                changed = True
        if changed:
            full_path.write_text(patched, encoding="utf-8")
            print(f"  🔗 Rewrote static re-exports in {full_path.name}")

    def _process_stylesheets(self, soup: BeautifulSoup):
        """Download external stylesheets and rewrite URLs inside them."""
        print("\n🎨 Processing stylesheets...")
        for tag in soup.find_all("link", rel=lambda v: v and "stylesheet" in v):
            href = tag.get("href")
            if not href:
                continue
            root_rel = self._download_asset(href, "css")
            if root_rel:
                # _process_css_file needs disk-relative path (no leading /)
                disk_rel = root_rel.lstrip("/")
                self._process_css_file(disk_rel)
                tag["href"] = root_rel  # root-relative for correct resolution from any subpage

        # Process inline <style> tags
        for tag in soup.find_all("style"):
            if tag.string:
                tag.string = self._rewrite_css_urls(tag.string)

    def _process_css_file(self, disk_path: str):
        """Parse a CSS file and download any url() references.
        disk_path is relative to self.output_dir (no leading slash)."""
        full_path = self.output_dir / disk_path
        if not full_path.exists():
            return
        css_text = full_path.read_text(encoding="utf-8", errors="ignore")
        new_css = self._rewrite_css_urls(css_text)
        full_path.write_text(new_css, encoding="utf-8")

    def _rewrite_css_urls(self, css: str) -> str:
        """Find url() in CSS and download/rewrite them to root-relative paths.

        Root-relative paths (/fonts/...) are used instead of file-relative ones
        so that fonts and images resolve correctly when the CSS is inline on a
        subpage (e.g. /work/project/index.html) or hosted via any static server.
        """
        def replace_url(match):
            url = match.group(1).strip("'\"")
            if url.startswith("data:") or url.startswith("#"):
                return match.group(0)
            self._download_asset(url)
            full_url = urljoin(self.url + "/", url)
            # downloaded[] stores root-relative paths (/fonts/..., /images/..., etc.)
            root_rel = self.downloaded.get(full_url)
            if root_rel:
                return f"url('{root_rel}')"
            return match.group(0)

        return re.sub(r'url\(([^)]+)\)', replace_url, css)

    def _process_scripts(self, soup: BeautifulSoup):
        """Download external JS files, stripping Framer editor/CMS/badge scripts."""
        print("\n📜 Processing scripts...")
        for tag in soup.find_all("script"):
            src = tag.get("src", "")
            text = tag.string or ""
            combined = (src + text).lower()

            # Remove Framer editor, CMS, badge, and analytics scripts
            if any(x in combined for x in EDITOR_SCRIPT_PATTERNS):
                print(f"  🗑️  Removed Framer script: {src[:80] or text[:60]}")
                tag.decompose()
                continue

            # Download remaining external scripts
            if src:
                local = self._download_asset(src, "js")
                if local:
                    tag["src"] = local

        # Remove modulepreload links for Framer editor modules
        for link in soup.find_all("link", rel="modulepreload"):
            href = (link.get("href") or "").lower()
            if any(x in href for x in FRAMER_SKIP_PATTERNS):
                print(f"  🗑️  Removed editor modulepreload: {href[:80]}")
                link.decompose()

    def _process_images(self, soup: BeautifulSoup):
        """Download images from img, source, picture tags."""
        print("\n🖼️  Processing images...")
        for tag in soup.find_all("img"):
            for attr in ["src", "data-src", "data-framer-original-src"]:
                url = tag.get(attr)
                if url:
                    local = self._download_asset(url, "images")
                    if local:
                        tag[attr] = local
            # Handle srcset
            srcset = tag.get("srcset")
            if srcset:
                tag["srcset"] = self._rewrite_srcset(srcset)

        for tag in soup.find_all("source"):
            srcset = tag.get("srcset")
            if srcset:
                tag["srcset"] = self._rewrite_srcset(srcset)
            src = tag.get("src")
            if src:
                local = self._download_asset(src)
                if local:
                    tag["src"] = local

    def _rewrite_srcset(self, srcset: str) -> str:
        """Rewrite srcset attribute URLs."""
        parts = []
        for entry in srcset.split(","):
            entry = entry.strip()
            if not entry:
                continue
            tokens = entry.split()
            url = tokens[0]
            descriptor = " ".join(tokens[1:]) if len(tokens) > 1 else ""
            local = self._download_asset(url, "images")
            if local:
                parts.append(f"{local} {descriptor}".strip())
            else:
                parts.append(entry)
        return ", ".join(parts)

    def _process_videos(self, soup: BeautifulSoup):
        """Download video assets."""
        print("\n🎬 Processing videos...")
        for tag in soup.find_all("video"):
            for attr in ["src", "poster"]:
                url = tag.get(attr)
                if url:
                    subfolder = "images" if attr == "poster" else "videos"
                    local = self._download_asset(url, subfolder)
                    if local:
                        tag[attr] = local
            for source in tag.find_all("source"):
                src = source.get("src")
                if src:
                    local = self._download_asset(src, "videos")
                    if local:
                        source["src"] = local

    def _process_fonts(self, soup: BeautifulSoup):
        """Download font preloads."""
        print("\n🔤 Processing fonts...")
        for tag in soup.find_all("link", rel="preload"):
            if tag.get("as") == "font":
                href = tag.get("href")
                if href:
                    local = self._download_asset(href, "fonts")
                    if local:
                        tag["href"] = local

    def _process_links(self, soup: BeautifulSoup):
        """Process link tags (favicons, manifests, etc)."""
        print("\n🔗 Processing link tags...")
        for tag in soup.find_all("link"):
            rel = tag.get("rel", [])
            if isinstance(rel, list):
                rel = " ".join(rel)
            if any(x in rel for x in ["icon", "apple-touch-icon", "manifest"]):
                href = tag.get("href")
                if href:
                    local = self._download_asset(href, "assets")
                    if local:
                        tag["href"] = local

    def _process_meta_tags(self, soup: BeautifulSoup):
        """Download OG images and other meta assets."""
        print("\n🏷️  Processing meta tags...")
        for tag in soup.find_all("meta"):
            content = tag.get("content", "")
            prop = tag.get("property", "") or tag.get("name", "")
            if "image" in prop and content.startswith("http"):
                local = self._download_asset(content, "images")
                if local:
                    tag["content"] = local

    def _process_inline_styles(self, soup: BeautifulSoup):
        """Process inline style attributes for background-image etc."""
        print("\n🎨 Processing inline styles...")
        for tag in soup.find_all(style=True):
            style = tag["style"]
            if "url(" in style:
                tag["style"] = self._rewrite_css_urls(style)

    def _download_network_resources(self):
        """Download additional resources captured during page rendering."""
        print("\n🌐 Downloading captured network resources...")
        for res in self.network_resources:
            url = res["url"]
            ct = res.get("content_type", "")

            # Skip Framer CMS, editor, analytics, badge URLs
            if any(pat in url.lower() for pat in FRAMER_SKIP_PATTERNS):
                continue

            # Only download asset types we care about
            is_asset = any(x in ct for x in [
                "font", "image", "video", "audio",
                "css", "javascript", "svg", "woff", "webp"
            ])
            is_framer = any(d in url for d in FRAMER_DOMAINS)
            if (is_asset or is_framer) and url not in self.downloaded:
                self._download_asset(url)

    def _rewrite_html(self, html: str) -> str:
        """Final pass: rewrite any remaining absolute URLs in the HTML.
        self.downloaded stores root-relative paths (/images/x.png) for all assets."""
        for original_url, root_rel_path in self.downloaded.items():
            html = html.replace(original_url, root_rel_path)
            # Also try without protocol
            for prefix in ["https:", "http:"]:
                if original_url.startswith(prefix):
                    html = html.replace(original_url[len(prefix):], root_rel_path)
        return html

    def _post_process_js_files(self):
        """Final pass over every downloaded JS/MJS file.

        Runs after all pages have been scraped so self.downloaded is fully
        populated.  Rewrites any remaining remote URLs (Google Fonts,
        framerusercontent modules, etc.) to local root-relative paths and
        strips residual Framer-specific strings that aren't actual assets.
        """
        print("\n🔧 Post-processing JS files for remaining remote URLs...")
        # rglob("*.js") misses versioned names like Robot.js@0.0.57 — collect all files
        # and filter by whether ".js" or ".mjs" appears in the name.
        js_files = [
            f for f in self.output_dir.rglob("*")
            if f.is_file() and re.search(r'\.m?js', f.name)
        ]
        patched_count = 0

        for full_path in js_files:
            try:
                text = full_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            patched = text

            # Rewrite every URL that was successfully downloaded
            for original_url, root_rel in self.downloaded.items():
                if original_url in patched:
                    patched = patched.replace(original_url, root_rel)
                # Protocol-relative variant (//framerusercontent.com/...)
                for prefix in ("https:", "http:"):
                    if original_url.startswith(prefix):
                        proto_rel = original_url[len(prefix):]
                        if proto_rel in patched:
                            patched = patched.replace(proto_rel, root_rel)

            # Editor canvas screenshot placeholder
            patched = patched.replace("screenshot.framer.invalid", "about:blank")

            # Fix protocol-less external URLs used as href values.
            # Framer sometimes stores bare "domain.com/path" in component props and
            # relies on its Link component to add https:// at runtime. Since we strip
            # the live Framer runtime, we must add the protocol ourselves.
            patched = re.sub(
                r"""(href:[`'"])([a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/[^`'"\\]*)""",
                lambda m: m.group(0) if m.group(2).startswith(
                    ("https://", "http://", "/", "#", "data:", "mailto:", "tel:")
                ) else m.group(1) + "https://" + m.group(2),
                patched,
            )

            if patched != text:
                full_path.write_text(patched, encoding="utf-8")
                patched_count += 1

        print(f"  ✅ Post-processed {patched_count} JS files")

    def _cleanup_framer(self, html: str) -> str:
        """Aggressively remove all Framer branding, CMS, editor, and badge elements."""
        print("\n🧹 Cleaning up Framer remnants...")
        soup = BeautifulSoup(html, "html.parser")
        removed_count = 0

        # ── 1. Remove "Made with Framer" badge / watermark ──────────────────
        # Badge is usually an <a> linking to framer.com with badge-related text/classes
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            tag_str = str(a).lower()
            if "framer.com" in href and any(x in tag_str for x in [
                "badge", "made with", "made in", "built with",
                "powered by", "framer-badge", "__framer_badge",
            ]):
                # Remove the badge and its parent container if it's a wrapper
                parent = a.parent
                a.decompose()
                # If parent is now empty, remove it too
                if parent and parent.name not in ["body", "html", None]:
                    if not parent.get_text(strip=True) and not parent.find_all(["img", "video", "svg"]):
                        parent.decompose()
                removed_count += 1

        # Also catch badge containers by id/class patterns
        for el in soup.find_all(True):
            el_id = (el.get("id") or "").lower()
            el_class = " ".join(el.get("class", [])).lower()
            if any(x in el_id + el_class for x in [
                "framer-badge", "__framer_badge", "framer_badge",
                "badge-container", "made-with-framer",
            ]):
                el.decompose()
                removed_count += 1

        # ── 2. Remove Framer editor bar / edit overlay ──────────────────────
        for el in soup.find_all(True):
            el_id = (el.get("id") or "").lower()
            el_class = " ".join(el.get("class", [])).lower()
            data_attrs = " ".join(str(v) for k, v in el.attrs.items() if "framer" in k.lower())
            combined = el_id + el_class + data_attrs
            if any(x in combined for x in [
                "editorbar", "editor-bar", "framer-edit",
                "framer-toolbar", "framer-overlay",
            ]):
                el.decompose()
                removed_count += 1

        # ── 2b. Remove modulepreload links for Framer editor modules ─────────
        for link in soup.find_all("link", rel="modulepreload"):
            href = (link.get("href") or "").lower()
            if any(x in href for x in FRAMER_SKIP_PATTERNS):
                link.decompose()
                removed_count += 1

        # ── 2c. Remove preconnect hints to Google Fonts CDN (fonts are local) ──
        for link in soup.find_all("link", rel="preconnect"):
            href = (link.get("href") or "").lower()
            if "fonts.gstatic.com" in href or "fonts.googleapis.com" in href:
                link.decompose()
                removed_count += 1

        # ── 2d. Remove Google Fonts stylesheet <link> (fonts downloaded locally) ─
        for link in soup.find_all("link", rel=lambda v: v and "stylesheet" in v):
            href = (link.get("href") or "")
            if "fonts.googleapis.com" in href:
                link.decompose()
                removed_count += 1

        # ── 2e. Remove canonical / hreflang alternate links to original domain ──
        original_domain = self.parsed_url.netloc  # e.g. meetsmeet.framer.website
        for link in soup.find_all("link"):
            rel = " ".join(link.get("rel", []))
            href = link.get("href") or ""
            if ("canonical" in rel or "alternate" in rel) and (
                original_domain in href
                or "framer.website" in href
                or "framer.com" in href
            ):
                link.decompose()
                removed_count += 1

        # ── 3. Remove all remaining Framer tracking / CMS / editor scripts ──
        for script in soup.find_all("script"):
            text = script.string or ""
            src = script.get("src", "")
            combined = (text + src).lower()
            if any(x in combined for x in EDITOR_SCRIPT_PATTERNS):
                script.decompose()
                removed_count += 1

        # ── 4. Remove Framer CMS iframes and hidden editor elements ─────────
        for iframe in soup.find_all("iframe"):
            src = (iframe.get("src") or "").lower()
            if any(x in src for x in ["framer.com", "edit", "cms"]):
                iframe.decompose()
                removed_count += 1

        # ── 5. Remove Framer-specific data attributes (clean DOM) ───────────
        framer_attrs = [
            "data-framer-appear-id", "data-framer-component-type",
            "data-framer-cursor", "data-framer-generated",
            "data-framer-highlight", "data-framer-name",
            "data-framer-page-optimized-at", "data-framer-portal-id",
            "data-framer-target-size-id",
        ]
        for el in soup.find_all(True):
            for attr in list(el.attrs.keys()):
                if attr in framer_attrs or attr.startswith("data-framer-"):
                    del el[attr]

        # ── 6. Remove links pointing to framer.com (badge remnants) ─────────
        for a in soup.find_all("a", href=True):
            if "framer.com" in a["href"] and not a.get_text(strip=True):
                a.decompose()
                removed_count += 1

        # ── 7. Clean up Framer-related meta tags ────────────────────────────
        for meta in soup.find_all("meta"):
            name = (meta.get("name") or "").lower()
            content = (meta.get("content") or "").lower()
            if any(x in name + content for x in ["framer", "generator"]):
                if "framer" in name + content:
                    meta.decompose()
                    removed_count += 1

        # ── 8. Remove empty style/script tags left behind ───────────────────
        for tag in soup.find_all(["style", "script"]):
            if tag.string and not tag.string.strip():
                tag.decompose()

        # ── 8.5 Strip Framer editor bar CSS rules from <style> tags ─────────
        editorbar_css_patterns = [
            r'#__framer-editorbar[^{]*\{[^}]*\}',
            r'#__framer-editorbar-[a-z-]+[^{]*\{[^}]*\}',
            r'#__framer-badge[^{]*\{[^}]*\}',
            r'\.framer-badge[^{]*\{[^}]*\}',
        ]
        for tag in soup.find_all("style"):
            if tag.string:
                css = tag.string
                for pattern in editorbar_css_patterns:
                    css = re.sub(pattern, '', css, flags=re.DOTALL)
                # Also strip @media blocks that only contain editorbar rules
                css = re.sub(
                    r'@media[^{]*\{\s*#__framer-editorbar[^}]*\{[^}]*\}\s*\}',
                    '', css, flags=re.DOTALL
                )
                tag.string = css

        # ── 8.6 Remove Framer HTML comments ─────────────────────────────────
        html_str = str(soup)
        html_str = re.sub(
            r'<!--\s*(Start|End)\s+of\s+(headEnd|bodyStart|bodyEnd|headStart)[^>]*-->',
            '', html_str
        )
        # Replace screenshot.framer.invalid placeholder in the HTML
        html_str = html_str.replace("screenshot.framer.invalid", "about:blank")
        soup = BeautifulSoup(html_str, "html.parser")

        # ── 9. Add offline-friendly base tag and URL polyfill ───────────────
        head = soup.find("head")
        if head:
            existing_base = soup.find("base")
            if not existing_base:
                base_tag = soup.new_tag("base", href="./")
                head.insert(0, base_tag)

            # Add URL polyfill to fix relative URLs in Framer's JS
            polyfill_script = soup.new_tag("script")
            polyfill_script.string = '''
                const OriginalURL = window.URL;
                window.URL = new Proxy(OriginalURL, {
                    construct(target, args) {
                        try {
                            if (args[1] && typeof args[1] === 'string' && args[1].startsWith('/')) {
                                args[1] = window.location.origin + args[1];
                            }
                            return new target(...args);
                        } catch (e) {
                            return new target(args[0], args[1] || window.location.origin);
                        }
                    }
                });
            '''
            head.insert(1, polyfill_script)

        print(f"  🗑️  Removed {removed_count} Framer elements")
        return str(soup)


def main():
    parser = argparse.ArgumentParser(
        description="🕷️ Framer Site Scraper - Download Framer sites for offline use",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python framer_scraper.py https://meetsmeet.framer.website/
  python framer_scraper.py mysite.framer.website
        """
    )
    parser.add_argument("website", help="URL of the Framer website to scrape")
    parser.add_argument("-o", "--output", default="./scrap",
                        help="Base output directory (default: ./scrap)")

    args = parser.parse_args()

    scraper = FramerScraper(args.website, args.output)
    scraper.run()


if __name__ == "__main__":
    main()
