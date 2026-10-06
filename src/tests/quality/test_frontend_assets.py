"""Public script URLs and CommonJS dependencies resolve after asset moves."""
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / 'src/web'


class ScriptSources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.sources.append(dict(attrs).get('src', ''))


def test_local_page_scripts_are_served_at_their_declared_urls():
    from api.web_chat_api import app
    client = app.test_client()
    checked = set()
    for page in WEB.glob('*.html'):
        parser = ScriptSources()
        parser.feed(page.read_text(encoding='utf-8'))
        for source in parser.sources:
            url = urlsplit(source)
            if not source or url.netloc or not url.path.lstrip('/').startswith('js/'):
                continue
            path = url.path.lstrip('/')
            assert (WEB / path).is_file(), (page.name, source)
            if path not in checked:
                response = client.get('/' + path)
                assert response.status_code == 200, (page.name, source)
                assert response.data == (WEB / path).read_bytes()
                checked.add(path)
    assert checked


def test_relative_commonjs_dependencies_exist():
    for script in (WEB / 'js').rglob('*.js'):
        for relative in re.findall(r"require\(['\"](\.[^'\"]+\.js)['\"]\)", script.read_text()):
            assert (script.parent / relative).is_file(), (script, relative)
