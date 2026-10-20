"""Tiny, standard-library client for NSE India's public JSON endpoints.

NSE's website serves its data through the same /api/... URLs its own pages
call. They refuse requests that do not look like a browser: you need a
User-Agent, and the cookies NSE sets when you first open the home page.
This module does exactly that and nothing more.

- No API key, no login, nothing to pip install.
- Polite: at most ~2 requests a second, one retry after re-warming cookies.
- Unofficial: NSE can change these endpoints without notice. When a call
  fails, every tool in this pack also accepts a file you download by hand
  from nseindia.com (the same page has a "Download (.csv)" link).
"""

import gzip
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.nseindia.com"
ARCHIVES = "https://nsearchives.nseindia.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MIN_GAP = 0.5  # seconds between requests


class NSEError(RuntimeError):
    pass


class NSE:
    def __init__(self, timeout=20):
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.opener.addheaders = [
            ("User-Agent", UA),
            ("Accept", "application/json, text/plain, */*"),
            ("Accept-Language", "en-US,en;q=0.9"),
            ("Accept-Encoding", "gzip"),
            ("Referer", BASE + "/"),
        ]
        self._warm = False
        self._last = 0.0

    def _open(self, url):
        gap = time.time() - self._last
        if gap < MIN_GAP:
            time.sleep(MIN_GAP - gap)
        self._last = time.time()
        try:
            resp = self.opener.open(url, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            raise NSEError("%s -> HTTP %s" % (url, e.code)) from None
        except urllib.error.URLError as e:
            raise NSEError("%s -> %s" % (url, e.reason)) from None
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip" or data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return data

    def warm(self):
        """Open the home page once so NSE sets its session cookies."""
        self._open(BASE + "/")
        self._warm = True

    def get_json(self, path, params=None):
        """GET https://www.nseindia.com/api/<path>?<params> and parse JSON."""
        url = "%s/api/%s" % (BASE, path.lstrip("/"))
        if params:
            url += "?" + urllib.parse.urlencode(params)
        for attempt in (1, 2):
            if not self._warm:
                self.warm()
            try:
                raw = self._open(url)
                return json.loads(raw.decode("utf-8"))
            except (NSEError, ValueError):
                if attempt == 2:
                    raise
                self._warm = False  # cookies may have expired; try once more
        return None

    def get_bytes(self, url):
        if not self._warm and url.startswith(BASE):
            self.warm()
        return self._open(url)


def ddmmyyyy(d):
    """NSE date parameters use DD-MM-YYYY."""
    return d.strftime("%d-%m-%Y")
