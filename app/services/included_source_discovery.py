"""Bounded discovery from an official index, without invented URLs or paid search."""
from html.parser import HTMLParser
import re
from urllib.parse import urljoin

from app.services import included_research_sources as sources
from app.services.production_spend import SpendBlocked

INDEX = 'https://ikeamuseum.com/en/explore/the-story-of-ikea/'
MAX_LINKS = 120


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag != 'a' or len(self.urls) >= MAX_LINKS:
            return
        url = urljoin(INDEX, dict(attrs).get('href') or '')
        # Exact same collection, one slug, short enough to retain two URLs in
        # a topic. Reject placeholders, queries, fragments and offsite links.
        if not re.fullmatch(re.escape(INDEX) + r'[a-z0-9]+(?:-[a-z0-9]+)*/', url) or len(url) > 100:
            return
        try:
            sources._url(url)
        except SpendBlocked:
            return
        if url not in self.urls:
            self.urls.append(url)


def discover_candidates(history, rotation):
    if type(rotation) is not int or not 0 <= rotation < 240:
        raise ValueError('Invalid source discovery rotation')
    try:
        actual, body, content_type = sources._get(INDEX)
        if actual != INDEX or content_type not in {'text/html', 'application/xhtml+xml'}:
            return []
        parser = _Links()
        parser.feed(body.decode('utf-8', errors='replace'))
    except SpendBlocked:
        return []
    urls = sorted(parser.urls, key=lambda url: sum(url in topic for topic in history))
    if len(urls) < 2:
        return []
    offset = (rotation * 2) % len(urls)
    urls = urls[offset:] + urls[:offset]
    return [{'url': url, 'category': 'discovered_primary'} for url in urls[:2]]
