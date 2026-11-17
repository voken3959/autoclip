"""Offline tests for chat badges (no network or IRC needed).

Run directly:  python tests/test_badges.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.badges import BadgeStore
from core.monitor import Monitor
from core.twitch_chat import TwitchChatClient


def make_store():
    return BadgeStore(client_id="", client_secret="", get_channel_id=lambda: "")


def test_resolve_uses_static_fallback():
    store = make_store()
    badges = store.resolve("moderator/1,subscriber/12")
    assert [b["type"] for b in badges] == ["moderator", "subscriber"]
    assert all(b["url"].startswith("https://") for b in badges)
    assert badges[0]["title"] == "Moderator"
    assert badges[1]["version"] == "12"


def test_resolve_skips_unknown_badges():
    store = make_store()
    badges = store.resolve("someunknown/1")
    assert badges == []


def test_resolve_channel_badges_win():
    store = make_store()
    store._channel = {
        "subscriber": {
            "0": {"url": "https://cdn/channel-sub-0.png", "title": "Channel Sub"},
        }
    }
    store._global = {
        "subscriber": {
            "0": {"url": "https://cdn/global-sub-0.png", "title": "Global Sub"},
        },
        "moderator": {
            "1": {"url": "https://cdn/mod.png", "title": "Moderator"},
        },
    }

    badges = store.resolve("subscriber/0,moderator/1")
    assert badges[0]["url"] == "https://cdn/channel-sub-0.png"
    assert badges[0]["title"] == "Channel Sub"
    # Version not present falls back to the first available version.
    assert badges[1]["url"] == "https://cdn/mod.png"


def test_parse_privmsg_includes_badges():
    line = (
        "@badge-info=;badges=moderator/1,subscriber/12;color=#FF0000;"
        "display-name=Alice;emotes=;id=abc;mod=1;room-id=123;"
        "subscriber=1;tmi-sent-ts=1;turbo=0;user-id=42;user-type= "
        ":alice!alice@alice.tmi.twitch.tv PRIVMSG #jynxzi :hello"
    )
    parsed = TwitchChatClient._parse_privmsg(TwitchChatClient("u", "t", "c", None), line)
    assert parsed is not None
    assert parsed["badges"] == "moderator/1,subscriber/12"
    assert parsed["username"] == "alice"
    assert parsed["display_name"] == "Alice"


def test_monitor_carries_badges():
    monitor = Monitor()
    monitor.badges_resolver = lambda tag: [{"type": "moderator", "url": "u", "title": "Moderator"}]

    monitor.add_message({"username": "alice", "text": "hi", "badges": "moderator/1"})
    entry = monitor.chat_messages[-1]
    assert entry["badges"] == [{"type": "moderator", "url": "u", "title": "Moderator"}]

    # A message without badges gets an empty list.
    monitor.add_message({"username": "bob", "text": "hi"})
    assert monitor.chat_messages[-1]["badges"] == []

    # A failing resolver must never break chat.
    monitor.badges_resolver = lambda tag: 1 / 0
    monitor.add_message({"username": "carol", "text": "hi", "badges": "x/1"})
    assert monitor.chat_messages[-1]["badges"] == []


if __name__ == "__main__":
    tests = [
        test_resolve_uses_static_fallback,
        test_resolve_skips_unknown_badges,
        test_resolve_channel_badges_win,
        test_parse_privmsg_includes_badges,
        test_monitor_carries_badges,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"\nAll {len(tests)} badge tests passed.")
