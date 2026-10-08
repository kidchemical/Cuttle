"""Page-header standard: one backbone governs shell page headers.

`src/web/css/compact_page.css` owns the title/subtitle type scale
(`.compact-page-title`, 1.05rem/0.78rem). Product shell pages reuse it;
page CSS owns layout and content below the header, never the header
type scale. There are no header variants: no full-bleed banners, eyebrow
rows, or title icons; title and subtitle share one line (wrapping only
when narrow). Chat keeps its own header.

The same backbone owns the page frame: every shell page root carries
`.cuttle-page` (inset + max width from `--page-*` tokens), and page CSS
never re-declares padding/margin/max-width on that root. Git is a
full-height tool surface: it stays full width but uses the inset tokens.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / 'src/web'

# Full shell pages with headers. Excluded on purpose: chat_page (own
# header by design), terminal_page (PTY tool surface, no header),
# query_log (standalone inspector, no header), achievements_preview
# (dev probe, not a product page).
PAGES = [
    'jobs_page.html',
    'settings_page.html',
    'projects_page.html',
    'apps_page.html',
    'router_editor.html',
    'gizmos_page.html',
    'achievements_page.html',
    'dashboards_page.html',
    'agent_feed.html',
    'git_graph_page.html',
]

# Full-height tool surfaces that use the frame tokens without `.cuttle-page`.
FRAME_EXEMPT = {'git_graph_page.html'}
FRAME_PROPS = re.compile(r'(?<![\w-])(padding|margin|max-width)\s*:')

# The only header class that satisfies the standard.
STANDARD_HEADERS = ('compact-page-title',)

# Pictographs in a page title (🏆, 🔀, …) — titles are plain text.
EMOJI = re.compile('[\U0001F300-\U0001FAFF\u2600-\u27BF]')

# Shared stylesheets that are not page-owned: theme/legacy/component
# layers never set product header type.
SHARED_CSS = {
    'ui_boot.css',
    'shared_navigation.css',
    'landing_page.css',
    'themes.css',
    'safe_area.css',
    'video_background.css',
    'auth_modal.css',
    'chat_lightbox.css',
    'chat_voice.css',
    'diff_modal.css',
    'git_commit_viewer.css',
    'pending_changes.css',
    'storage_settings.css',
    'query_log_inspector.css',
}

# Backbone title size is 1.05rem (~16.8px); allow rounding headroom only.
MAX_H1_PX = 19.2


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stylesheets = []
        self.scripts = []
        self.inline_style = []

    def handle_starttag(self, tag, attrs):
        if tag == 'link':
            attrs = dict(attrs)
            if attrs.get('rel') == 'stylesheet' and attrs.get('href'):
                self.stylesheets.append(attrs['href'].split('?')[0])
        elif tag == 'script':
            src = dict(attrs).get('src', '').split('?')[0]
            if src:
                self.scripts.append(src)

    def handle_data(self, data):
        pass


def _parse(page):
    parser = _Links()
    text = (WEB / page).read_text(encoding='utf-8')
    parser.feed(text)
    inline = re.findall(r'<style[^>]*>(.*?)</style>', text, re.S)
    return parser.stylesheets, inline, text


def _h1_sizes(css_text):
    """Yield header-h1 font sizes (px) from rules whose selector names h1."""
    for selector, size, unit in re.findall(
        r'([^{}]+)\{\s*[^{}]*?font-size\s*:\s*([\d.]+)(px|rem)',
        css_text,
    ):
        if re.search(r'(?<![\w-])h1(?![\w-])', selector):
            value = float(size) * (16.0 if unit == 'rem' else 1.0)
            yield selector.strip(), value


def test_shell_pages_load_the_header_backbone():
    missing = [
        page
        for page in PAGES
        if not any(
            href.lstrip('/').endswith('css/compact_page.css')
            for href, in [(h,) for h in _parse(page)[0]]
        )
    ]
    assert not missing, f'pages missing compact_page.css backbone: {missing}'


def test_backbone_header_token_is_pinned():
    backbone = (WEB / 'css' / 'compact_page.css').read_text(encoding='utf-8')
    assert re.search(
        r'\.compact-page-title h1\s*\{[^}]*font-size:\s*1\.05rem', backbone
    ), 'backbone .compact-page-title h1 must stay 1.05rem'


def test_page_headers_use_the_standard_class():
    offenders = []
    for page in PAGES:
        sheets, _, text = _parse(page)
        if any(cls in text for cls in STANDARD_HEADERS):
            continue
        # JS-rendered headers (dashboards): follow the page's own scripts.
        parser = _Links()
        parser.feed(text)
        found = False
        for src in parser.scripts:
            path = WEB / src.lstrip('/')
            if path.is_file() and any(
                cls in path.read_text(encoding='utf-8')
                for cls in STANDARD_HEADERS
            ):
                found = True
                break
        if not found:
            offenders.append(page)
    assert not offenders, f'headers not on the standard: {offenders}'


def test_no_page_redefines_an_oversized_header_h1():
    offenders = []
    for page in PAGES:
        sheets, inline_blocks, _ = _parse(page)
        own = [
            (WEB / href.lstrip('/')).read_text(encoding='utf-8')
            for href in sheets
            if href.lstrip('/').startswith('css/')
            and href.rsplit('/', 1)[-1] not in SHARED_CSS
            and (WEB / href.lstrip('/')).is_file()
        ]
        for css_text in own + inline_blocks:
            for selector, px in _h1_sizes(css_text):
                if px > MAX_H1_PX:
                    offenders.append((page, selector, px))
    assert not offenders, f'oversized page header h1: {offenders}'


def _frame_root_classes(text):
    match = re.search(r'class="([^"]*\bcuttle-page\b[^"]*)"', text)
    if not match:
        return None
    return [c for c in match.group(1).split() if c != 'cuttle-page']


def _own_css(page):
    sheets, inline_blocks, _ = _parse(page)
    own = [
        (WEB / href.lstrip('/')).read_text(encoding='utf-8')
        for href in sheets
        if href.lstrip('/').startswith('css/')
        and href.rsplit('/', 1)[-1] not in SHARED_CSS | {'compact_page.css'}
        and (WEB / href.lstrip('/')).is_file()
    ]
    return own + inline_blocks


def test_shell_pages_use_the_shared_frame():
    missing = [
        page
        for page in PAGES
        if page not in FRAME_EXEMPT and _frame_root_classes(_parse(page)[2]) is None
    ]
    assert not missing, f'pages missing the .cuttle-page frame root: {missing}'


def test_backbone_owns_the_frame_tokens():
    backbone = (WEB / 'css' / 'compact_page.css').read_text(encoding='utf-8')
    rule = re.search(r'body\.compact-shell-page \.cuttle-page\s*\{([^}]*)\}', backbone)
    assert rule, 'compact_page.css must define the .cuttle-page frame'
    for token in ('--page-max-width', '--page-pad-x', '--page-pad-top'):
        assert token in rule.group(1), f'frame must use {token}'


def test_no_page_redefines_its_frame():
    offenders = []
    for page in PAGES:
        roots = _frame_root_classes(_parse(page)[2]) or []
        for css_text in _own_css(page):
            for root in roots:
                pattern = r'(?:^|[{},])\s*\.%s\s*\{([^{}]*)\}' % re.escape(root)
                for body in re.findall(pattern, css_text):
                    if FRAME_PROPS.search(body):
                        offenders.append((page, root, body.strip()[:80]))
    assert not offenders, f'page CSS re-declares the shared frame: {offenders}'


def _page_titles(page):
    text = _parse(page)[2]
    sources = [text]
    parser = _Links()
    parser.feed(text)
    for src in parser.scripts:
        path = WEB / src.lstrip('/')
        if src.startswith('/js/dashboards/') and path.is_file():
            sources.append(path.read_text(encoding='utf-8'))
    for source in sources:
        yield from re.findall(r'<h1[^>]*>(.*?)</h1>', source, re.S)


def test_page_titles_are_plain_text():
    offenders = [
        (page, title)
        for page in PAGES
        for title in _page_titles(page)
        if EMOJI.search(title)
    ]
    assert not offenders, f'icons in page titles: {offenders}'


def test_no_banner_or_eyebrow_header_chrome():
    offenders = []
    for page in PAGES:
        text = _parse(page)[2]
        for marker in ('re-header', 'feed-eyebrow', 're-logo'):
            if marker in text:
                offenders.append((page, marker))
    assert not offenders, f'page-owned header chrome: {offenders}'


def test_dashboards_back_is_the_round_icon_button():
    js = (WEB / 'js/dashboards/dashboards_page.js').read_text(encoding='utf-8')
    assert 'All dashboards</button>' not in js
    assert 'class="page-icon-btn dash-back"' in js
    backbone = (WEB / 'css' / 'compact_page.css').read_text(encoding='utf-8')
    assert re.search(r'\.page-icon-btn\s*\{[^}]*border-radius:\s*50%', backbone)
