"""Where the data comes from.

Two questions need outside data:
  - Who counts as an answer?   -> Wikidata, via a SPARQL query
  - How familiar is each one?  -> Wikimedia page-view statistics

Both are slow to fetch and change slowly, so everything is cached as plain
JSON in cache/. A cache this simple is easy to inspect, easy to delete,
and needs no database server. See DECISIONS.md, "No database yet".

There are two interchangeable sources with the same two methods
(sparql and pageviews):
  LiveSource     the real APIs, used for actual builds
  FixtureSource  saved fake responses in a folder, used by the tests and
                 the offline run configuration, so the whole pipeline can be
                 checked without an internet connection
build_prompt.py doesn't care which one it's given.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import config


class IncompleteDownload(Exception):
    """A download stopped before everything was fetched: page views, or a
    Wikidata query the service gave up on.

    Raised instead of carrying on with gaps, because tiers built from
    partial data would be wrong. Whatever was fetched is already saved in
    the cache, so running the build again picks up where this one stopped.
    build_prompt.main() turns this into a plain message, not a traceback.
    """


class QueryTooSlow(IncompleteDownload):
    """Wikidata gave up on a query because it ran past its 60-second limit.

    Retrying the same query would just use up more of the time budget, so
    batched lookups catch this and split the batch instead.
    """


class _RateLimited(Exception):
    """Wikimedia keeps refusing requests: no point trying more articles now."""


class _ServerError(Exception):
    """One article kept failing with server errors: skip it, try the rest."""


def pageview_window(months: int = config.PAGEVIEW_MONTHS, today: date | None = None,
                    last_month: str | None = None) -> tuple[date, date]:
    """The last `months` full calendar months, ending with last month.

    The current month is left out because it's incomplete: including it
    would make results depend on which day of the month you ran the build.
    last_month ("2026-10") fixes the end instead, so the window, and with it
    the page-view cache, only moves when you choose (config.PAGEVIEW_LAST_MONTH).
    """
    if last_month:
        year, month = (int(x) for x in last_month.split("-"))
        first_of_next = date(year + month // 12, month % 12 + 1, 1)
        end = first_of_next - timedelta(days=1)
    else:
        today = today or date.today()
        end = today.replace(day=1) - timedelta(days=1)   # last day of last month
    year, month = end.year, end.month - months + 1
    while month <= 0:                                 # step back across years
        month += 12
        year -= 1
    return date(year, month, 1), end


def retry_after_seconds(header: str | None) -> float | None:
    """Read a Retry-After header, which may be seconds or an HTTP date."""
    if not header:
        return None
    try:
        return max(0.0, float(header))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(header)
        return max(0.0, when.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class LiveSource:
    """Talks to the real Wikidata and Wikimedia APIs."""

    def __init__(self, refresh: bool = False):
        # Refuse to start with the placeholder contact address (see config.py).
        if "example.com" in config.CONTACT:
            raise SystemExit(
                "Put your email address in a file called contact.txt in the "
                "project folder first (or set PPM_CONTACT). Wikimedia asks every "
                "script to identify itself."
            )
        self.refresh = refresh
        self._http = None
        self._last_request = 0.0
        self._sparql_ready_at = 0.0   # when the next Wikidata query may start
        self._sparql_slowdown = 1     # doubles with each 429, for the rest of the run
        self.category_via: dict[str, dict[str, str]] = {}
        config.CACHE_DIR.mkdir(exist_ok=True)

    def _session(self):
        """One HTTP session for the whole run (it reuses connections)."""
        # Imported here rather than at the top so the offline tests can run
        # even before `requests` is installed.
        import requests

        if self._http is None:
            self._http = requests.Session()
            # Wikimedia's recommended format ends with the HTTP library and
            # its version, e.g. "python-requests/2.32.3".
            self._http.headers["User-Agent"] = (
                f"{config.USER_AGENT} python-requests/{requests.__version__}"
            )
        return self._http

    def _pace(self) -> None:
        """Wait so requests start at least MIN_REQUEST_INTERVAL apart."""
        wait = self._last_request + config.MIN_REQUEST_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _fresh_connection(self) -> None:
        """Forget open connections, so the next request opens a new one.

        Servers close connections that sit idle. Reusing one of those fails
        with "Remote end closed connection without response", which is what
        stopped a build after a two-minute rate-limit wait.
        """
        if self._http is not None:
            self._http.close()
            self._http = None

    def _send(self, request, what: str):
        """Make one request (calling request()), riding out dropped connections.

        A dropped connection or a timeout is retried on a fresh connection
        after each wait in config.CONNECTION_RETRY_WAITS. If it keeps
        happening, the build stops with a plain message instead of a
        traceback, and as everywhere else, what's fetched so far is saved.
        """
        import requests

        hiccups = (requests.ConnectionError, requests.Timeout,
                   requests.exceptions.ChunkedEncodingError)
        failures = 0
        while True:
            try:
                return request()
            except hiccups as problem:
                failures += 1
                self._fresh_connection()
                if failures > len(config.CONNECTION_RETRY_WAITS):
                    raise IncompleteDownload(
                        f"Lost the connection to {what} {failures} times in a row "
                        f"({type(problem).__name__}). Check your internet connection. "
                        f"Everything fetched so far is saved, so running the build "
                        f"again carries on from here.") from None
                wait = config.CONNECTION_RETRY_WAITS[failures - 1]
                print(f"  lost the connection to {what}; trying again in {wait} s...")
                time.sleep(wait)

    # ---- Wikidata -------------------------------------------------------

    def _cache_file(self, name: str, query: str) -> Path:
        fingerprint = hashlib.sha1(query.encode("utf-8")).hexdigest()[:8]
        return config.CACHE_DIR / f"wikidata_{name}_{fingerprint}.json"

    def is_cached(self, name: str, query: str) -> bool:
        """Whether this query's answer is already saved (so it won't be sent)."""
        return not self.refresh and self._cache_file(name, query).exists()

    def remember(self, name: str, query: str, rows: list[dict]) -> None:
        """Cache rows for a query that was answered some other way.

        Used when a batch had to be split: the halves' rows together are the
        whole batch's answer, so a later run finds the whole batch cached and
        doesn't try (and fail) the slow query again.
        """
        self._cache_file(name, query).write_text(json.dumps(rows, ensure_ascii=False, indent=1))

    def sparql(self, name: str, query: str) -> list[dict]:
        """Run a SPARQL query and return its rows as plain dicts.

        Results are cached as cache/wikidata_<name>_<fingerprint>.json, where
        the fingerprint comes from the query text. Editing the query, or
        extras.csv (which changes the extras query), therefore re-queries
        automatically, while an unchanged query is never re-sent: Wikidata
        changes slowly and the query service is a shared public resource.
        Use --refresh to pick up changes made on Wikidata itself.

        Rate limits, outages and slow queries are handled as config.py
        describes (SPARQL_REST_FACTOR and below).
        """
        cache_file = self._cache_file(name, query)
        if cache_file.exists() and not self.refresh:
            return json.loads(cache_file.read_text())

        rate_limited = outages = 0
        attempt = 0
        while True:
            # Rest after the last query in proportion to how long it took.
            wait = self._sparql_ready_at - time.monotonic()
            if wait > 30:
                # Say so, so a long rest never looks like the build has hung.
                print(f"  resting {wait:.0f} s before the next query "
                      f"(the last one was slow)...")
            if wait > 0:
                time.sleep(wait)
            attempt += 1
            print(f"  querying Wikidata ({name})..." if attempt == 1
                  else f"  querying Wikidata ({name}) again, try {attempt}...")
            self._pace()
            started = time.monotonic()
            # POST rather than GET: queries with long VALUES lists (the extras)
            # can exceed the length limit for a URL.
            resp = self._send(lambda: self._session().post(
                config.WIKIDATA_SPARQL,
                data={"query": query},
                headers={"Accept": "application/sparql-results+json"},
                # The service stops every query at 60 s, so a minute and a half
                # with no answer means something's wrong: give up and retry
                # (_send) rather than sit there silently for longer.
                timeout=90,
            ), "Wikidata")
            took = time.monotonic() - started
            status = resp.status_code
            if status in (429, 502, 503):
                # Refused without running the query, so none of our budget was
                # used: the wait below is the whole pause, with no rest on top.
                self._sparql_ready_at = 0.0
            else:
                rest = max(config.SPARQL_MIN_GAP, took * config.SPARQL_REST_FACTOR)
                self._sparql_ready_at = time.monotonic() + rest * self._sparql_slowdown

            if status == 429:
                rate_limited += 1
                if rate_limited > config.SPARQL_RATE_LIMIT_RETRIES:
                    raise IncompleteDownload(
                        f"Wikidata kept asking us to slow down (HTTP 429) on the '{name}' "
                        f"query. Everything fetched so far is saved: wait 15 minutes or so "
                        f"and run the build again to carry on from here.")
                wait = min(retry_after_seconds(resp.headers.get("Retry-After")) or 60,
                           config.MAX_RATE_LIMIT_WAIT)
                print(f"  Wikidata asked us to slow down; waiting {wait:.0f} s "
                      f"({rate_limited} of {config.SPARQL_RATE_LIMIT_RETRIES})...")
                if self._sparql_slowdown < config.SPARQL_MAX_SLOWDOWN:
                    self._sparql_slowdown *= 2
                    print(f"  (and resting {self._sparql_slowdown}x as long between "
                          f"queries for the rest of this run)")
                time.sleep(wait)
                self._fresh_connection()   # the old one has sat idle through the wait
                continue
            if status in (500, 504):
                raise QueryTooSlow(
                    f"Wikidata gave up on the '{name}' query: it ran past the service's "
                    f"60-second limit (HTTP {status}). The query in queries/ needs to be "
                    f"made lighter.")
            if status in (502, 503):
                outages += 1
                if outages > config.SPARQL_SERVER_RETRIES:
                    raise IncompleteDownload(
                        f"Wikidata's query service is unavailable (HTTP {status}). "
                        f"Everything fetched so far is saved: try again later.")
                wait = 15 * 2 ** (outages - 1)
                print(f"  Wikidata is busy (HTTP {status}); trying again in {wait} s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        # The raw format wraps every value as {"type": ..., "value": ...};
        # flatten it to {column: value}, which is all we need.
        rows = [
            {key: cell["value"] for key, cell in binding.items()}
            for binding in resp.json()["results"]["bindings"]
        ]
        cache_file.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
        return rows

    # ---- Wikipedia lists: categories and infobox users ---------------------

    def _wikipedia_list(self, params: dict, key: str, what: str):
        """Every item of a Wikipedia API list, following "continue" for more.

        Each request returns up to 500 items. A refusal stops the build
        cleanly (IncompleteDownload) rather than carrying on with half a list.
        """
        params = {**params, "action": "query", "format": "json", "formatversion": "2"}
        refusals = 0
        while True:
            self._pace()
            resp = self._send(lambda: self._session().get(
                config.WIKIPEDIA_API, params=params, timeout=60), "Wikipedia")
            if resp.status_code == 429 or resp.status_code >= 500:
                # Busy or briefly down: wait as asked (or 15, 30, 60 s) and retry.
                refusals += 1
                if refusals <= config.SPARQL_SERVER_RETRIES:
                    wait = min(retry_after_seconds(resp.headers.get("Retry-After"))
                               or 15 * 2 ** (refusals - 1), config.MAX_RATE_LIMIT_WAIT)
                    print(f"  Wikipedia is busy (HTTP {resp.status_code}); "
                          f"trying again in {wait:.0f} s...")
                    time.sleep(wait)
                    self._fresh_connection()
                    continue
            if resp.status_code != 200:
                raise IncompleteDownload(
                    f"Wikipedia's API answered {resp.status_code} when listing {what}. "
                    f"Try again in a few minutes.")
            refusals = 0
            data = resp.json()
            yield from data["query"][key]
            if "continue" not in data:
                return
            params.update(data["continue"])   # where the next page starts

    def _cached_list(self, name: str, key: str, build) -> list[str]:
        """A cached list of titles: cache/wikipedia_<name>_<fingerprint>.json."""
        fingerprint = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
        cache_file = config.CACHE_DIR / f"wikipedia_{name}_{fingerprint}.json"
        if cache_file.exists() and not self.refresh:
            print(f"  using cached article list: {cache_file.name}")
            return json.loads(cache_file.read_text())
        titles = list(dict.fromkeys(build()))   # unique, in order
        cache_file.write_text(json.dumps(titles, ensure_ascii=False, indent=1))
        return titles

    def category_members(self, name: str, categories, depth: int = 0,
                         skip: tuple[str, ...] = ()) -> list[str]:
        """Titles of the articles in English Wikipedia categories.

        categories: one category or a list. depth: how many levels of
        subcategories to follow as well (0 = just the category itself).
        skip: subcategories whose title contains any of these words are not
        followed ("companies", say). Asks Wikipedia's own API
        (list=categorymembers).

        This replaced reading the category from inside a SPARQL query
        (wikibase:mwapi), which timed out on the query service every time
        (DECISIONS.md, 30).
        """
        roots = [categories] if isinstance(categories, str) else list(categories)
        # A single category with no subcategories keeps the fingerprint the
        # first version used, so lists already cached stay valid.
        key = roots[0] if len(roots) == 1 and not depth and not skip else json.dumps(
            [roots, depth, sorted(skip)], ensure_ascii=False)

        via: dict[str, str] = {}   # article -> the (sub)category it was found in

        def build():
            seen: set[str] = set()
            level = roots
            for d in range(depth + 1):
                below = []
                for category in level:
                    if category in seen:
                        continue
                    seen.add(category)
                    print(f"  listing Wikipedia's {category}...")
                    params = {"list": "categorymembers", "cmtitle": category,
                              "cmnamespace": "0|14", "cmtype": "page|subcat", "cmlimit": "max"}
                    for m in self._wikipedia_list(params, "categorymembers", category):
                        if m["ns"] == 0:
                            via.setdefault(m["title"], category)
                            yield m["title"]
                        elif d < depth and not any(w.lower() in m["title"].lower() for w in skip):
                            below.append(m["title"])
                level = below

        titles = self._cached_list(name, key, build)
        # Which subcategory each article came from, for the review CSV: kept
        # in its own file, so article lists cached before it existed stay as
        # they are (and with them the Wikidata lookups made from them).
        fingerprint = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
        via_file = config.CACHE_DIR / f"categoryvia_{name}_{fingerprint}.json"
        if via_file.exists() and not self.refresh:
            via.update(json.loads(via_file.read_text()))
        else:
            if not via:   # the list came from the cache: list again to see where from
                for _ in build():
                    pass
            via_file.write_text(json.dumps(via, ensure_ascii=False, indent=1))
        self.category_via[name] = via
        return titles

    def category_sources(self, name: str) -> dict[str, str]:
        """Article -> the category it was listed in, after category_members(name)."""
        return self.category_via.get(name, {})

    def template_users(self, name: str, templates: list[str]) -> list[str]:
        """Titles of the articles that use any of these templates (infoboxes).

        "Every article with a chemical infobox" (Template:Chembox, Template:
        Infobox drug) is the most complete list of compounds Wikipedia has,
        because categories are organised by family and miss some.
        """
        def build():
            for template in templates:
                print(f"  listing articles that use {template}...")
                params = {"list": "embeddedin", "eititle": template,
                          "einamespace": "0", "eilimit": "max"}
                for m in self._wikipedia_list(params, "embeddedin", template):
                    yield m["title"]

        return self._cached_list(name, json.dumps(templates, ensure_ascii=False), build)

    # ---- Page views -----------------------------------------------------

    def _views_for(self, title: str, start: date, end: date) -> int:
        """Total views of one English Wikipedia article over the window.

        Raises _RateLimited if Wikimedia keeps refusing (stop everything)
        and _ServerError if this one article keeps failing (skip it).
        """
        # Titles go into the URL path, so spaces become underscores (Wikipedia's
        # own convention) and everything else is percent-encoded, including
        # "/": an article like "AC/DC" would otherwise be read as two path parts.
        encoded = urllib.parse.quote(title.replace(" ", "_"), safe="")
        # "user" excludes traffic Wikimedia identifies as bots and crawlers, so
        # the numbers reflect people reading. "monthly" keeps the response small.
        url = (
            f"{config.PAGEVIEWS_API}/en.wikipedia/all-access/user/{encoded}"
            f"/monthly/{start:%Y%m%d}00/{end:%Y%m%d}00"
        )
        rate_limit_waits = 0
        server_errors = 0
        while True:
            self._pace()
            resp = self._send(lambda: self._session().get(url, timeout=30),
                              "Wikimedia's page-view service")

            if resp.status_code == 404:
                # The API answers 404 when an article had no recorded views in
                # the window (new or very obscure articles). That's a real
                # answer, zero, not an error.
                return 0

            if resp.status_code == 429:
                # Rate-limited. This is about us, not about this article, so
                # retrying other articles wouldn't help: wait as long as
                # Wikimedia asks, and give up on the whole run if it keeps
                # refusing or asks for a long wait.
                rate_limit_waits += 1
                wait = retry_after_seconds(resp.headers.get("Retry-After")) or 60.0
                if rate_limit_waits > config.RATE_LIMIT_RETRIES or wait > config.MAX_RATE_LIMIT_WAIT:
                    raise _RateLimited(wait)
                print(f"    Wikimedia rate limit reached; waiting {wait:.0f} s "
                      f"(attempt {rate_limit_waits} of {config.RATE_LIMIT_RETRIES})")
                time.sleep(wait)
                self._fresh_connection()   # the old one has sat idle through the wait
                continue

            if resp.status_code >= 500:
                # A temporary server problem: back off (2, 4, 8, 16 s) and
                # retry, then skip this article rather than stop the run.
                server_errors += 1
                if server_errors > config.SERVER_ERROR_RETRIES:
                    raise _ServerError(resp.status_code)
                time.sleep(2 ** server_errors)
                continue

            resp.raise_for_status()   # any other error is a bug worth seeing
            return sum(item["views"] for item in resp.json().get("items", []))

    def pageviews(self, titles: list[str], start: date, end: date) -> dict[str, int]:
        """Total English Wikipedia views per article title (cached).

        Requests go one at a time, as the page-view API's documentation asks.
        The cache file name includes the window, so a new month automatically
        starts a fresh cache instead of mixing old and new numbers. Progress is
        saved every 50 articles and whenever the download stops, so an
        interrupted run loses almost nothing.
        """
        cache_file = config.CACHE_DIR / f"pageviews_en_{start:%Y%m}_{end:%Y%m}.json"
        cache: dict[str, int] = json.loads(cache_file.read_text()) if cache_file.exists() else {}
        # dict.fromkeys removes duplicate titles while keeping their order.
        todo = [t for t in dict.fromkeys(titles) if t not in cache]

        def save() -> None:
            cache_file.write_text(json.dumps(cache))

        if not todo:
            print(f"  using cached page views: {cache_file.name}")
            return {t: cache[t] for t in titles}

        print(f"  fetching page views for {len(todo)} articles "
              f"({len(cache)} already cached)...")
        skipped: list[str] = []
        started = time.monotonic()
        for done, title in enumerate(todo, 1):
            try:
                cache[title] = self._views_for(title, start, end)
            except _RateLimited as stop:
                save()
                fetched = done - 1 - len(skipped)
                raise IncompleteDownload(
                    f"Wikimedia's rate limit stopped the download after {fetched} of "
                    f"{len(todo)} articles (it asked for a {stop.args[0]:.0f} s wait).\n"
                    f"Nothing is lost: everything fetched so far is saved in cache/.\n"
                    f"Run the build again later (an hour is usually plenty) and it "
                    f"carries on from there."
                ) from None
            except _ServerError:
                skipped.append(title)
            except IncompleteDownload:
                save()                      # the connection kept dropping: keep progress
                raise

            if done % 50 == 0 or done == len(todo):
                save()
                rate = done / max(time.monotonic() - started, 1e-9)
                left = (len(todo) - done) / rate
                print(f"    {done}/{len(todo)}  (about {left / 60:.0f} min left)")

        if skipped:
            shown = ", ".join(skipped[:5]) + (" ..." if len(skipped) > 5 else "")
            raise IncompleteDownload(
                f"{len(skipped)} articles kept failing with server errors: {shown}\n"
                f"Everything else is saved in cache/. Run the build again to retry "
                f"just those."
            )
        return {t: cache[t] for t in titles}


class FixtureSource:
    """Reads saved responses from a folder instead of the internet.

    Expects <name>.json for each SPARQL query (rows in the same flattened
    shape LiveSource.sparql returns) and pageviews.json mapping article
    title -> total views. A missing query file counts as no rows.
    """

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def sparql(self, name: str, query: str) -> list[dict]:
        path = self.folder / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else []

    def is_cached(self, name: str, query: str) -> bool:
        return False   # fixtures aren't a cache: nothing is "saved from an earlier run"

    def remember(self, name: str, query: str, rows: list[dict]) -> None:
        pass   # fixtures are read-only

    def category_members(self, name: str, categories, depth: int = 0, skip=()) -> list[str]:
        path = self.folder / f"{name}_category.json"
        return json.loads(path.read_text()) if path.exists() else []

    def category_sources(self, name: str) -> dict[str, str]:
        path = self.folder / f"{name}_via.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def template_users(self, name: str, templates: list[str]) -> list[str]:
        path = self.folder / f"{name}_template.json"
        return json.loads(path.read_text()) if path.exists() else []

    def pageviews(self, titles: list[str], start: date, end: date) -> dict[str, int]:
        views = json.loads((self.folder / "pageviews.json").read_text())
        return {t: views.get(t, 0) for t in titles}
