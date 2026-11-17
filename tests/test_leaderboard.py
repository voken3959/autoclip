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

    # Fake an active IRC connection for the first stream.
    class FakeConn:
        channels = ["big"]

    collector._connections.append(FakeConn())

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


if __name__ == "__main__":
    tests = [
        test_privmsg_regex,
        test_rolling_window_counts_only_recent_uniques,
        test_prune_removes_expired_channels,
        test_snapshot_computes_contribution,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"\nAll {len(tests)} leaderboard tests passed.")
