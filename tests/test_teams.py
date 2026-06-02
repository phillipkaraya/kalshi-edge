"""Team name resolution and Kalshi market parsing."""

from __future__ import annotations

from datetime import date

from kalshi_edge.data.teams import parse_kalshi_game, parse_ticker_date, resolve_team


def test_resolve_city_full_nickname_tricode() -> None:
    assert resolve_team("San Antonio") == "SAS"
    assert resolve_team("San Antonio Spurs") == "SAS"
    assert resolve_team("Spurs") == "SAS"
    assert resolve_team("SAS") == "SAS"
    assert resolve_team("New York") == "NYK"
    assert resolve_team("New York Knicks") == "NYK"


def test_resolve_la_disambiguation() -> None:
    assert resolve_team("LA Lakers") == "LAL"
    assert resolve_team("Los Angeles Clippers") == "LAC"
    # Bare "Los Angeles" is ambiguous -> unresolved rather than wrong.
    assert resolve_team("Los Angeles") is None
    assert resolve_team("Definitely Not A Team") is None


def test_parse_ticker_date() -> None:
    assert parse_ticker_date("KXNBAGAME-26JUN10SASNYK-SAS") == date(2026, 6, 10)
    assert parse_ticker_date("NO-DATE-HERE") is None


def test_parse_kalshi_game_at_format() -> None:
    pg = parse_kalshi_game(
        "Game 4: San Antonio at New York Winner?", "San Antonio", "KXNBAGAME-26JUN10SASNYK-SAS"
    )
    assert pg is not None
    assert pg.away == "SAS"  # "X at Y" => X away
    assert pg.home == "NYK"
    assert pg.yes_tricode == "SAS"
    assert pg.game_date == date(2026, 6, 10)
