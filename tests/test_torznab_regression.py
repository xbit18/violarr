from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

import app as app_module
from settings import SettingsStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "SETTINGS_STORE",
        SettingsStore(path=tmp_path / "settings.json", environ={}),
    )
    return TestClient(app_module.app)


def sample_row(title="Example", torrent_type="movie"):
    return (title, 1234, 7, "provider", None, "abc123", torrent_type)


def test_caps_contract_is_unchanged(client):
    response = client.get("/api", params={"t": "caps"})

    root = ElementTree.fromstring(response.content)
    assert response.status_code == 200
    assert root.tag == "caps"
    assert root.find("limits").attrib == {"max": "200", "default": "100"}
    assert root.find("searching/search").attrib["supportedParams"] == "q"
    assert root.find("searching/movie-search").attrib["supportedParams"] == "q,imdbid,tmdbid"
    assert root.find("searching/tv-search").attrib["supportedParams"] == "q,season,ep,imdbid,tmdbid"
    assert root.find("server").attrib["title"] == "Violarr"


def test_rss_channel_uses_violarr_branding(client, monkeypatch):
    monkeypatch.setattr(app_module, "query_generic", lambda *args: [sample_row()])

    response = client.get("/api", params={"t": "search", "q": "example"})

    channel = ElementTree.fromstring(response.content).find("channel")
    assert channel.findtext("title") == "Violarr"
    assert channel.findtext("description") == "L’integrazione Prowlarr per Il Corsaro Viola"


@pytest.mark.parametrize(
    ("params", "query_name", "expected_args"),
    [
        (
            {"t": "search", "q": "avatar", "limit": 20, "offset": 4},
            "query_generic",
            ("avatar", 20, 4),
        ),
        (
            {"t": "movie", "q": "avatar", "imdbid": "123", "tmdbid": 10, "limit": 20, "offset": 4},
            "query_movie",
            ("tt123", 10, "avatar", 20, 4),
        ),
        (
            {
                "t": "tvsearch",
                "q": "show",
                "imdbid": "tt456",
                "season": 2,
                "ep": 3,
                "limit": 20,
                "offset": 4,
            },
            "query_tv",
            ("tt456", "show", 2, 3, None, 20, 4),
        ),
    ],
)
def test_unfiltered_requests_preserve_query_and_xml_contract(
    client, monkeypatch, params, query_name, expected_args
):
    calls = []

    def query(*args):
        calls.append(args)
        return [sample_row(torrent_type="anime" if params["t"] == "tvsearch" else "movie")]

    monkeypatch.setattr(app_module, query_name, query)

    response = client.get("/api", params=params)

    root = ElementTree.fromstring(response.content)
    item = root.find("channel/item")
    attrs = {
        element.attrib["name"]: element.attrib["value"]
        for element in item
        if element.tag.endswith("attr")
    }
    assert response.status_code == 200
    assert calls == [expected_args]
    assert item.findtext("guid") == "abc123"
    assert item.findtext("link") == "magnet:?xt=urn:btih:abc123"
    assert attrs["seeders"] == "7"
    assert attrs["category"] == ("5070" if params["t"] == "tvsearch" else "2000")


def test_ranked_processing_uses_bounded_window_then_paginates(client, monkeypatch):
    settings = app_module.SETTINGS_STORE.load()
    settings["result_processing"]["preset"] = "italian_preferred"
    app_module.SETTINGS_STORE.save(settings)
    calls = []

    def query(*args):
        calls.append(args)
        return [sample_row("Fallback"), sample_row("Movie.ITA")]

    monkeypatch.setattr(app_module, "query_generic", query)

    response = client.get("/api", params={"t": "search", "limit": 1, "offset": 0})

    root = ElementTree.fromstring(response.content)
    assert calls == [(None, app_module.RESULT_CANDIDATE_WINDOW, 0)]
    assert [item.findtext("title") for item in root.findall("channel/item")] == ["Movie.ITA"]


@pytest.mark.parametrize(
    ("offset", "limit", "expected_query_offsets", "expected_titles"),
    [
        (250, 2, [0], ["Result 250", "Result 251"]),
        (1000, 2, [1000], ["Result 1000", "Result 1001"]),
        (
            995,
            10,
            [0, 1000],
            [
                "Result 995",
                "Result 996",
                "Result 997",
                "Result 998",
                "Result 999",
                "Result 1000",
                "Result 1001",
                "Result 1002",
                "Result 1003",
                "Result 1004",
            ],
        ),
    ],
)
def test_ranked_pagination_uses_non_overlapping_windows(
    client,
    monkeypatch,
    offset,
    limit,
    expected_query_offsets,
    expected_titles,
):
    settings = app_module.SETTINGS_STORE.load()
    settings["result_processing"]["preset"] = "italian_preferred"
    app_module.SETTINGS_STORE.save(settings)
    calls = []

    def query(q, query_limit, query_offset):
        calls.append((q, query_limit, query_offset))
        return [
            (
                f"Result {index}",
                1234,
                7,
                "provider",
                None,
                f"hash-{index}",
                "movie",
            )
            for index in range(query_offset, query_offset + query_limit)
        ]

    monkeypatch.setattr(app_module, "query_generic", query)

    response = client.get(
        "/api",
        params={"t": "search", "limit": limit, "offset": offset},
    )

    root = ElementTree.fromstring(response.content)
    assert calls == [
        (None, app_module.RESULT_CANDIDATE_WINDOW, query_offset)
        for query_offset in expected_query_offsets
    ]
    assert [item.findtext("title") for item in root.findall("channel/item")] == expected_titles


def test_filtered_pagination_fetches_the_window_for_large_offsets(client, monkeypatch):
    settings = app_module.SETTINGS_STORE.load()
    settings["result_processing"]["preset"] = "italian_only"
    app_module.SETTINGS_STORE.save(settings)
    calls = []

    def query(q, query_limit, query_offset):
        calls.append((q, query_limit, query_offset))
        return [
            (
                f"Movie.ITA.{index}",
                1234,
                7,
                "provider",
                None,
                f"hash-{index}",
                "movie",
            )
            for index in range(query_offset, query_offset + query_limit)
        ]

    monkeypatch.setattr(app_module, "query_generic", query)

    response = client.get(
        "/api",
        params={"t": "search", "limit": 1, "offset": 1000},
    )

    root = ElementTree.fromstring(response.content)
    assert calls == [(None, app_module.RESULT_CANDIDATE_WINDOW, 1000)]
    assert [item.findtext("title") for item in root.findall("channel/item")] == ["Movie.ITA.1000"]


def test_subtitle_correction_adds_language_only_when_enabled(client, monkeypatch):
    titles = [
        "Rick.and.Morty.S09E04.[SUB ITA]",
        "Rick.and.Morty.S09E04.ITA.ENG",
        "Rick.and.Morty.S09E04.MULTI.[SUB ITA]",
    ]
    monkeypatch.setattr(
        app_module, "query_generic", lambda *args: [sample_row(title) for title in titles]
    )

    def items():
        response = client.get("/api", params={"t": "search", "q": "Rick"})
        assert response.status_code == 200
        result = []
        for item in ElementTree.fromstring(response.content).findall("channel/item"):
            attrs = {
                entry.attrib["name"]: entry.attrib["value"]
                for entry in item
                if entry.tag.endswith("attr")
            }
            result.append((item.findtext("title"), attrs))
        return result

    disabled = items()
    assert [title for title, _ in disabled] == titles
    assert all("language" not in attrs for _, attrs in disabled)

    settings = app_module.SETTINGS_STORE.load()
    settings["result_processing"]["subtitle_language_correction"] = True
    app_module.SETTINGS_STORE.save(settings)
    enabled = items()
    assert [title for title, _ in enabled] == titles
    assert enabled[0][1]["language"] == "English"
    assert "language" not in enabled[1][1]
    assert "language" not in enabled[2][1]
