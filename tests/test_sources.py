"""LiveSource with the network replaced by a fake, so no internet needed.

These pin down how the downloader behaves when Wikimedia says no: rate
limits pause and then stop cleanly with progress saved, server errors skip
one article, and nothing ever crashes halfway with work lost.
"""
from datetime import date

import pytest

import config
import sources
from sources import IncompleteDownload, LiveSource, QueryTooSlow, pageview_window, retry_after_seconds

WINDOW = (date(2024, 11, 1), date(2026, 10, 31))


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Answers page-view requests from a script of responses per title.

    script maps a title fragment to a list of responses to give in turn; the
    last one repeats. Unscripted titles get 123 views.
    """

    def __init__(self, script=None):
        self.script = script or {}
        self.urls = []

    def get(self, url, timeout, params=None):
        if params and params.get("list") == "categorymembers":
            cat = params["cmtitle"]
            if cat == "Category:Name reactions":          # two pages, to test "continue"
                if "cmcontinue" not in params:
                    return FakeResponse(200, {"query": {"categorymembers": [
                        {"ns": 0, "title": "Grignard reaction"},
                        {"ns": 14, "title": "Category:Rearrangement reactions"},
                        {"ns": 14, "title": "Category:Reaction companies"}]},
                        "continue": {"cmcontinue": "page|2", "continue": "-||"}})
                return FakeResponse(200, {"query": {"categorymembers": [{"ns": 0, "title": "Wittig reaction"}]}})
            members = {"Category:Rearrangement reactions": ["Cope rearrangement", "Grignard reaction"],
                       "Category:Reaction companies": ["Acme Reactions Ltd"]}.get(cat, [])
            return FakeResponse(200, {"query": {"categorymembers": [{"ns": 0, "title": m} for m in members]}})
        if params and params.get("list") == "embeddedin":
            users = {"Template:Chembox": ["Benzene", "Toluene"], "Template:Infobox drug": ["Aspirin", "Benzene"]}
            return FakeResponse(200, {"query": {"embeddedin": [{"ns": 0, "title": u} for u in users[params["eititle"]]]}})
        self.urls.append(url)
        for fragment, responses in self.script.items():
            if f"/{fragment}/" in url:
                return responses.pop(0) if len(responses) > 1 else responses[0]
        return FakeResponse(200, {"items": [{"views": 100}, {"views": 23}]})

    def post(self, url, data, headers, timeout):
        assert "SELECT" in data["query"]
        return FakeResponse(200, {"results": {"bindings": [
            {"item": {"value": "http://www.wikidata.org/entity/Q1"},
             "label": {"value": "quartz"}, "title": {"value": "Quartz"},
             "sitelinks": {"value": "120"}},
        ]}})


@pytest.fixture
def make_live(tmp_path, monkeypatch):
    """A LiveSource wired to a FakeSession, with sleeps recorded, not slept."""
    monkeypatch.setattr(config, "CONTACT", "test@example.org")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    sleeps = []
    monkeypatch.setattr(sources.time, "sleep", sleeps.append)

    def make(script=None):
        src = LiveSource()
        fake = FakeSession(script)
        src._session = lambda: fake
        return src, fake, sleeps, tmp_path
    return make


def test_refuses_to_run_without_a_contact_address(monkeypatch):
    monkeypatch.setattr(config, "CONTACT", "your-email@example.com")
    with pytest.raises(SystemExit):
        LiveSource()


def test_user_agent_follows_wikimedia_format(monkeypatch, tmp_path):
    pytest.importorskip("requests")
    monkeypatch.setattr(config, "CONTACT", "test@example.org")
    monkeypatch.setattr(config, "USER_AGENT", "PartsPerMillionPipeline/0.2 (test@example.org)")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    agent = LiveSource()._session().headers["User-Agent"]
    assert agent.startswith("PartsPerMillionPipeline/0.2 (test@example.org) python-requests/")


def test_pageview_window_is_last_24_full_months():
    assert pageview_window(24, today=date(2026, 11, 19)) == WINDOW
    assert pageview_window(12, today=date(2026, 1, 5)) == (date(2025, 1, 1), date(2025, 12, 31))


def test_retry_after_parsing():
    assert retry_after_seconds("7") == 7
    assert retry_after_seconds(None) is None
    assert retry_after_seconds("soon") is None
    assert retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") == 0  # a date in the past


def test_sparql_parses_and_caches(make_live):
    src, fake, _, cache = make_live()
    rows = src.sparql("minerals", "SELECT ?item WHERE {}")
    assert rows == [{"item": "http://www.wikidata.org/entity/Q1", "label": "quartz",
                     "title": "Quartz", "sitelinks": "120"}]
    assert len(list(cache.glob("wikidata_minerals_*.json"))) == 1


def test_changing_a_query_requeries_but_same_query_is_cached(make_live):
    src, fake, _, cache = make_live()
    calls = []
    original_post = fake.post
    fake.post = lambda *a, **k: calls.append(1) or original_post(*a, **k)
    src.sparql("minerals", "SELECT ?item WHERE {}")
    src.sparql("minerals", "SELECT ?item WHERE {}")
    assert len(calls) == 1
    src.sparql("minerals", "SELECT ?item ?label WHERE {}")   # e.g. extras.csv edited
    assert len(calls) == 2


def test_titles_are_encoded_and_404_means_zero(make_live):
    src, fake, _, _ = make_live({"Nonexistentite": [FakeResponse(404)]})
    views = src.pageviews(["Native gold", "AC/DC", "Nonexistentite"], *WINDOW)
    assert views == {"Native gold": 123, "AC/DC": 123, "Nonexistentite": 0}
    assert any("/Native_gold/monthly/2024110100/2026103100" in u for u in fake.urls)
    assert any("/AC%2FDC/" in u for u in fake.urls)  # slashes must be encoded


def test_second_run_uses_the_cache(make_live):
    src, fake, _, _ = make_live()
    src.pageviews(["Quartz"], *WINDOW)
    calls = len(fake.urls)
    assert src.pageviews(["Quartz"], *WINDOW) == {"Quartz": 123}
    assert len(fake.urls) == calls


def test_rate_limit_waits_as_asked_then_carries_on(make_live):
    src, fake, sleeps, _ = make_live({
        "Galena": [FakeResponse(429, headers={"Retry-After": "7"}),
                   FakeResponse(200, {"items": [{"views": 5}]})],
    })
    assert src.pageviews(["Quartz", "Galena"], *WINDOW) == {"Quartz": 123, "Galena": 5}
    assert 7 in sleeps


def test_persistent_rate_limit_stops_cleanly_and_keeps_progress(make_live):
    import json
    src, fake, _, cache = make_live({"Galena": [FakeResponse(429)]})
    with pytest.raises(IncompleteDownload, match="rate limit"):
        src.pageviews(["Quartz", "Galena", "Pyrite"], *WINDOW)
    saved = json.loads(next(cache.glob("pageviews_*.json")).read_text())
    assert saved == {"Quartz": 123}             # work before the stop is kept
    assert not any("/Pyrite/" in u for u in fake.urls)  # no hammering afterwards

    # Next run (limit lifted) only fetches what's missing.
    src2, fake2, _, _ = make_live()
    assert src2.pageviews(["Quartz", "Galena", "Pyrite"], *WINDOW)["Pyrite"] == 123
    assert not any("/Quartz/" in u for u in fake2.urls)


def test_long_retry_after_stops_immediately(make_live):
    src, fake, sleeps, _ = make_live({"Galena": [FakeResponse(429, headers={"Retry-After": "3600"})]})
    with pytest.raises(IncompleteDownload):
        src.pageviews(["Galena"], *WINDOW)
    assert 3600 not in sleeps


def test_server_errors_skip_one_article_but_finish_the_rest(make_live):
    import json
    src, fake, _, cache = make_live({"Galena": [FakeResponse(503)]})
    with pytest.raises(IncompleteDownload, match="Galena"):
        src.pageviews(["Quartz", "Galena", "Pyrite"], *WINDOW)
    saved = json.loads(next(cache.glob("pageviews_*.json")).read_text())
    assert saved == {"Quartz": 123, "Pyrite": 123}


def test_contact_comes_from_file_or_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.delenv("PPM_CONTACT", raising=False)
    assert "example.com" in config._contact()            # nothing set yet
    (tmp_path / "contact.txt").write_text("me@uni.example\n")
    assert config._contact() == "me@uni.example"
    monkeypatch.setenv("PPM_CONTACT", "env@uni.example")
    assert config._contact() == "env@uni.example"        # the variable wins


def test_wikidata_timeout_signals_too_slow_without_retrying(make_live):
    # A query past the 60-second limit isn't retried as it is (that would use
    # up more of the time budget): batched lookups split it instead.
    src, fake, _, cache = make_live()
    calls = []
    fake.post = lambda *a, **k: calls.append(1) or FakeResponse(504)
    with pytest.raises(QueryTooSlow):
        src.sparql("named_reactions", "SELECT ?item WHERE {}")
    assert len(calls) == 1
    assert not list(cache.glob("wikidata_named_reactions_*.json"))   # nothing cached


def test_wikidata_rate_limit_waits_as_asked_then_carries_on(make_live):
    src, fake, sleeps, _ = make_live()
    answers = [FakeResponse(429, headers={"Retry-After": "120"}), FakeResponse(503)]
    ok = fake.post
    fake.post = lambda *a, **k: answers.pop(0) if answers else ok(*a, **k)
    assert src.sparql("q", "SELECT ?item WHERE {}")[0]["title"] == "Quartz"
    assert 120 in sleeps and 15 in sleeps       # Retry-After honoured; outage backed off


def test_wikidata_rate_limit_gives_up_cleanly_after_several_tries(make_live):
    src, fake, _, _ = make_live()
    calls = []
    fake.post = lambda *a, **k: calls.append(1) or FakeResponse(429, headers={"Retry-After": "5"})
    with pytest.raises(IncompleteDownload, match="slow down"):
        src.sparql("q", "SELECT ?item WHERE {}")
    assert len(calls) == 1 + config.SPARQL_RATE_LIMIT_RETRIES


def test_rests_after_a_slow_query(make_live, monkeypatch):
    # After a query that took 10 s, the next one waits about 10 s more, which
    # keeps the build inside Wikidata's 60-seconds-per-minute budget.
    src, fake, sleeps, _ = make_live()
    clock = [1000.0]
    monkeypatch.setattr(sources.time, "monotonic", lambda: clock[0])
    ok = fake.post
    def slow_post(*a, **k):
        clock[0] += 10.0
        return ok(*a, **k)
    fake.post = slow_post
    src.sparql("first", "SELECT ?a WHERE {}")
    src.sparql("second", "SELECT ?b WHERE {}")
    assert any(abs(s - 10.0 * config.SPARQL_REST_FACTOR) < 0.01 for s in sleeps)


def test_category_members_follow_continuation_and_cache(make_live):
    src, fake, _, cache = make_live()
    assert src.category_members("named_reactions", "Category:Name reactions") == [
        "Grignard reaction", "Wittig reaction"]
    assert len(list(cache.glob("wikipedia_named_reactions_*.json"))) == 1
    fake.get = None      # a second call must come from the cache
    assert src.category_members("named_reactions", "Category:Name reactions")[1] == "Wittig reaction"


def test_category_tree_follows_subcategories_and_skips(make_live):
    src, _, _, _ = make_live()
    titles = src.category_members("t", "Category:Name reactions", depth=1, skip=("companies",))
    # Subcategory articles are included once each; skipped subcategories aren't read.
    assert titles == ["Grignard reaction", "Wittig reaction", "Cope rearrangement"]


def test_template_users_are_listed_once(make_live):
    src, _, _, _ = make_live()
    assert src.template_users("t", ["Template:Chembox", "Template:Infobox drug"]) == [
        "Benzene", "Toluene", "Aspirin"]


# ---- Dropped connections and repeat 429s (DECISIONS.md, 37) ---------------

def test_dropped_connection_is_retried_on_a_fresh_one(make_live):
    # The build that prompted this: a two-minute 429 wait, then the server had
    # closed the idle connection ("Remote end closed connection without response").
    import requests
    src, fake, sleeps, _ = make_live()
    ok, drops = fake.post, [1]
    def flaky_post(*a, **k):
        if drops:
            drops.pop()
            raise requests.ConnectionError("Remote end closed connection without response")
        return ok(*a, **k)
    fake.post = flaky_post
    assert src.sparql("q", "SELECT ?item WHERE {}")[0]["title"] == "Quartz"
    assert config.CONNECTION_RETRY_WAITS[0] in sleeps


def test_connection_that_keeps_dropping_stops_cleanly(make_live):
    import requests
    src, fake, _, cache = make_live()
    calls = []
    def dead_post(*a, **k):
        calls.append(1)
        raise requests.ConnectionError("no route to host")
    fake.post = dead_post
    with pytest.raises(IncompleteDownload, match="Lost the connection to Wikidata"):
        src.sparql("q", "SELECT ?item WHERE {}")
    assert len(calls) == 1 + len(config.CONNECTION_RETRY_WAITS)
    assert not list(cache.glob("wikidata_q_*.json"))


def test_quick_queries_still_rest_a_little(make_live, monkeypatch):
    src, _, sleeps, _ = make_live()
    clock = [1000.0]
    monkeypatch.setattr(sources.time, "monotonic", lambda: clock[0])   # queries take no time
    src.sparql("first", "SELECT ?a WHERE {}")
    src.sparql("second", "SELECT ?b WHERE {}")
    assert any(abs(s - config.SPARQL_MIN_GAP) < 0.01 for s in sleeps)


def test_a_429_slows_the_rest_of_the_run(make_live, monkeypatch):
    src, fake, sleeps, _ = make_live()
    clock = [1000.0]
    monkeypatch.setattr(sources.time, "monotonic", lambda: clock[0])
    answers = [FakeResponse(429, headers={"Retry-After": "120"})]
    ok = fake.post
    fake.post = lambda *a, **k: answers.pop(0) if answers else ok(*a, **k)
    src.sparql("first", "SELECT ?a WHERE {}")
    sleeps.clear()
    src.sparql("second", "SELECT ?b WHERE {}")
    assert any(abs(s - 2 * config.SPARQL_MIN_GAP) < 0.01 for s in sleeps)   # rests doubled


def test_slowdown_is_capped(make_live):
    src, fake, _, _ = make_live()
    for n in range(6):                       # six queries, each refused once
        answers = [FakeResponse(429, headers={"Retry-After": "1"})]
        ok = fake.post
        fake.post = lambda *a, ok=ok, answers=answers, **k: answers.pop(0) if answers else ok(*a, **k)
        src.sparql(f"q{n}", f"SELECT ?a{n} WHERE {{}}")
    assert src._sparql_slowdown == config.SPARQL_MAX_SLOWDOWN


def test_page_views_keep_progress_if_the_connection_dies(make_live):
    import json
    import requests
    src, fake, _, cache = make_live()
    ok = fake.get
    def get(url, timeout, params=None):
        if "/Pyrite/" in url:
            raise requests.ConnectionError("gone")
        return ok(url, timeout, params)
    fake.get = get
    with pytest.raises(IncompleteDownload, match="Lost the connection"):
        src.pageviews(["Quartz", "Pyrite", "Galena"], *WINDOW)
    saved = json.loads(next(cache.glob("pageviews_*.json")).read_text())
    assert saved == {"Quartz": 123}          # what was fetched before the drop is kept


def test_wikipedia_lists_wait_out_a_busy_spell(make_live):
    src, fake, sleeps, _ = make_live()
    ok, busy = fake.get, [FakeResponse(503)]
    fake.get = lambda url, timeout, params=None: busy.pop(0) if busy else ok(url, timeout, params)
    assert src.template_users("t", ["Template:Chembox"]) == ["Benzene", "Toluene"]
    assert 15 in sleeps


def test_rerun_says_which_batches_are_saved_and_skips_them(make_live, monkeypatch, capsys):
    import build_prompt
    monkeypatch.setattr(build_prompt, "BATCH_SIZE", 2)
    src, fake, _, _ = make_live()
    titles = [f"Compound {i}" for i in range(25)]                   # 13 batches
    calls = []
    ok = fake.post
    def post(*a, **k):
        calls.append(1)
        if len(calls) == 6:
            raise IncompleteDownload("stopped")                    # the first run stops at batch 6
        return ok(*a, **k)
    fake.post = post
    with pytest.raises(IncompleteDownload):
        build_prompt.lookup_titles(src, "big", titles)
    capsys.readouterr()
    calls.clear()
    fake.post = lambda *a, **k: calls.append(1) or ok(*a, **k)
    build_prompt.lookup_titles(src, "big", titles)
    out = capsys.readouterr().out
    assert "5 of 13 batches saved from an earlier run; carrying on from batch 6" in out
    assert len(calls) == 8                                         # only batches 6 to 13
    build_prompt.lookup_titles(src, "big", titles)
    assert "all 13 batches saved" in capsys.readouterr().out


def test_busy_wait_is_just_the_backoff(make_live, monkeypatch, capsys):
    # A 503 that took a long time to arrive used none of our budget, so the
    # pause before retrying is the 15 s backoff alone, and the retry says so.
    src, fake, sleeps, _ = make_live()
    clock = [1000.0]
    monkeypatch.setattr(sources.time, "monotonic", lambda: clock[0])
    src._sparql_slowdown = 4
    ok, busy = fake.post, [1]
    def post(*a, **k):
        if busy:
            busy.pop()
            clock[0] += 50.0                  # the refusal took 50 s to come back
            return FakeResponse(503)
        return ok(*a, **k)
    fake.post = post
    src.sparql("q", "SELECT ?item WHERE {}")
    assert sleeps == [15]
    assert "querying Wikidata (q) again, try 2" in capsys.readouterr().out


def test_pageview_window_can_be_fixed():
    # A fixed last month keeps the window (and the cache) still across month ends.
    assert pageview_window(24, today=date(2026, 12, 3), last_month="2026-10") == WINDOW
    assert pageview_window(12, last_month="2025-12") == (date(2025, 1, 1), date(2025, 12, 31))


def test_category_members_remember_which_subcategory(make_live):
    src, fake, _, cache = make_live()
    src.category_members("t", "Category:Name reactions", depth=1, skip=("companies",))
    via = src.category_sources("t")
    assert via["Grignard reaction"] == "Category:Name reactions"
    assert via["Cope rearrangement"] != "Category:Name reactions"          # from a subcategory
    # A list cached before this existed is kept, and listed again only to see where from.
    next(cache.glob("categoryvia_t_*.json")).unlink()
    src2, fake2, _, _ = make_live()
    src2.category_members("t", "Category:Name reactions", depth=1, skip=("companies",))
    assert src2.category_sources("t")["Cope rearrangement"] == via["Cope rearrangement"]
