"""Rotate verified official article collections without inventing source URLs."""
from html.parser import HTMLParser
import re
from urllib.parse import urljoin

from app.services import included_research_sources as sources
from app.services.production_spend import SpendBlocked

INDEX = 'https://ikeamuseum.com/en/explore/the-story-of-ikea/'
# One index read per attempt, using hosts already allowed by the source reader.
# The index identifies candidates only; each article must still be read and
# support the proposed question before factual/editorial review can approve it.
COLLECTIONS = (
    (INDEX, INDEX, True),
    ('https://www.lego.com/en-us/history', 'https://www.lego.com/en-us/history/articles/', False),
    ('https://www.ibm.com/history/advancing-humanity', 'https://www.ibm.com/history/', False),
    ('https://www.aboutamazon.com/news/operations', 'https://www.aboutamazon.com/news/operations/', False),
    ('https://www.coca-colacompany.com/about-us/history', 'https://www.coca-colacompany.com/about-us/history/', False),
)
MAX_LINKS = 120


class _Links(HTMLParser):
    def __init__(self, index=INDEX, prefix=INDEX, trailing_slash=True):
        super().__init__(convert_charrefs=True)
        self.index, self.prefix, self.trailing_slash = index, prefix, trailing_slash
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag != 'a' or len(self.urls) >= MAX_LINKS:
            return
        url = urljoin(self.index, dict(attrs).get('href') or '')
        # Exact same collection, one slug, short enough to retain two URLs in
        # a topic. Reject placeholders, queries, fragments and offsite links.
        pattern = re.escape(self.prefix) + r'[a-z0-9]+(?:-[a-z0-9]+)*' + ('/' if self.trailing_slash else '')
        if not re.fullmatch(pattern, url) or len(url) > 100 or url == self.index:
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
    index, prefix, trailing_slash = COLLECTIONS[rotation % len(COLLECTIONS)]
    try:
        actual, body, content_type = sources._get(index)
        if actual != index or content_type not in {'text/html', 'application/xhtml+xml'}:
            return []
        parser = _Links(index, prefix, trailing_slash)
        parser.feed(body.decode('utf-8', errors='replace'))
    except SpendBlocked:
        return []
    urls = sorted(parser.urls, key=lambda url: sum(url in topic for topic in history))
    if len(urls) < 2:
        return []
    offset = ((rotation // len(COLLECTIONS)) * 2) % len(urls)
    urls = urls[offset:] + urls[:offset]
    return [{'url': url, 'category': 'discovered_primary'} for url in urls[:2]]
