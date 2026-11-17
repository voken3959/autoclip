"""Offline tests for core/leaderboard.py (no network or IRC needed).

Run directly:  python tests/test_leaderboard.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.leaderboard import PRIVMSG_RE, LeaderboardCollector


def make_collector():
    return LeaderboardCollector(
        username="viewer",
        token="token123",
        client_id="id",
        client_secret="secret",
    )


def test_privmsg_regex():
    line = (
        "@badge-info=;badges=;display-name=Alice;id=abc;mod=0;room-id=123;"
        "subscriber=0;tmi-sent-ts=1;turbo=0;user-id=42;user-type= "
        ":alice!alice@alice.tmi.twitch.tv PRIVMSG #jynxzi :hello!"
    )
    match = PRIVMSG_RE.match(line)
    assert match is not None, "PRIVMSG line did not parse"
    _tags, username, channel = match.groups()
    assert username == "alice"
    assert channel == "jynxzi"


def test_rolling_window_counts_only_recent_uniques():
    collector = make_collector()
    now = time.monotonic()

    collector.note_message("streamer", "alice", at=now)
    collector.note_message("streamer", "alice", at=now)          # duplicate
    collector.note_message("streamer", "bob", at=now - 100)
    collector.note_message("streamer", "carol", at=now - 400)   # outside 300s window

    # window is 300s by default
    assert collector.chatters_for("streamer") == 2, collector.chatters_for("streamer")

    collector._prune()
    assert collector.chatters_for("streamer") == 2


def test_prune_removes_expired_channels():
    collector = make_collector()
    now = time.monotonic()
    collector.note_message("old", "alice", at=now - 1000)
    collector._prune()
    assert collector.chatters_for("old") == 0
    assert "old" not in collector._activity


def test_snapshot_computes_contribution():
    collector = make_collector()
    collector.streams = [
        {
            "login": "big",
            "name": "Big Stream",
            "game": "Game",
            "viewers": 1000,
            "thumbnail": "",
            "url": "https://twitch.tv/big",
        },
        {
            "login": "not-joined",
            "name": "Not Joined",
            "game": "Game",
            "viewers": 500,
            "thumbnail": "",
            "url": "https://twitch.tv/not-joined",
        },
    ]

    # Fake an active IRC connection for the first stream, joined long enough
    # to be measured.
    class FakeConn:
        channels = ["big"]

    collector._connections.append(FakeConn())
    collector.note_joined(["big"], time.monotonic() - 120)

    now = time.monotonic()
    collector.note_message("big", "alice", at=now)
    collector.note_message("big", "bob", at=now - 50)

    data = collector.snapshot()
    assert data["ok"] is True
    by_login = {s["login"]: s for s in data["streams"]}

    big = by_login["big"]
    assert big["collecting"] is True
    assert big["chatters"] == 2
    assert big["contribution"] == 0.2, big["contribution"]  # 2 / 1000 * 100

    other = by_login["not-joined"]
    assert other["collecting"] is False
    assert other["chatters"] is None
    assert other["contribution"] is None

    assert data["average_contribution"] == 0.2

    # Without any contribution data the average is null, not zero.
    collector2 = make_collector()
    collector2.streams = []
    assert collector2.snapshot()["average_contribution"] is None


def test_snapshot_orders_by_contribution():
    collector = make_collector()
    collector.streams = [
        {"login": "a", "name": "A", "game": "", "viewers": 100, "thumbnail": "", "url": ""},
        {"login": "b", "name": "B", "game": "", "viewers": 900, "thumbnail": "", "url": ""},
        {"login": "c", "name": "C", "game": "", "viewers": 10, "thumbnail": "", "url": ""},
        {"login": "d", "name": "D", "game": "", "viewers": 5, "thumbnail": "", "url": ""},
    ]

    class FakeConn:
        channels = ["a", "b", "c", "d"]

    collector._connections.append(FakeConn())
    now = time.monotonic()
    collector.note_joined(["a", "b", "c", "d"], now - 120)

    # a: 10/100 = 10%, b: 3/900 = 0.33%, c: 2/10 = 20%, d: not measured.
    for i in range(10):
        collector.note_message("a", f"x{i}", at=now)
    collector.note_message("b", "y1", at=now)
    collector.note_message("b", "y2", at=now)
    collector.note_message("b", "y3", at=now)
    collector.note_message("c", "z1", at=now)
    collector.note_message("c", "z2", at=now)
    # d is joined and has been sampled long enough: its real contribution is 0%.

    streams = collector.snapshot()["streams"]
    assert [s["login"] for s in streams] == ["c", "a", "b", "d"]
    assert [s["contribution"] for s in streams] == [20.0, 10.0, 0.33, 0.0]


def test_min_sample_shows_dash_until_measured():
    collector = make_collector()
    collector.streams = [
        {"login": "new", "name": "New", "game": "", "viewers": 100, "thumbnail": "", "url": ""},
        {"login": "talked", "name": "Talked", "game": "", "viewers": 100, "thumbnail": "", "url": ""},
    ]

    class FakeConn:
        channels = ["new", "talked"]

    collector._connections.append(FakeConn())
    # Both joined just now (no elapsed sample time).
    collector.note_joined(["new", "talked"], time.monotonic())

    collector.note_message("talked", "alice", at=time.monotonic())

    by_login = {s["login"]: s for s in collector.snapshot()["streams"]}
    # "new" has no messages and has not been sampled: dash, not a false 0.
    assert by_login["new"]["chatters"] is None
    assert by_login["new"]["contribution"] is None
    # "talked" has real messages: measured right away.
    assert by_login["talked"]["chatters"] == 1
    assert by_login["talked"]["contribution"] == 1.0


if __name__ == "__main__":
    tests = [
        test_privmsg_regex,
        test_rolling_window_counts_only_recent_uniques,
        test_prune_removes_expired_channels,
        test_snapshot_computes_contribution,
        test_snapshot_orders_by_contribution,
        test_min_sample_shows_dash_until_measured,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"\nAll {len(tests)} leaderboard tests passed.")
