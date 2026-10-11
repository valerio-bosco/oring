"""Telegram gateway end to end: fake Telegram API ↔ `oring gateways` ↔ daemon ↔ fake ACP agent."""

import pytest

from conftest import fake_acp_agent
from oring.gateways.telegram import to_html
from telegram_server import TOKEN, FakeTelegram, render_html

ME, STRANGER = 1001, 666


@pytest.fixture
def telegram(daemon, tmp_path):
    api = FakeTelegram()
    token = tmp_path / "token"
    token.write_text(TOKEN + "\n")

    def start(allowed=(ME,), agents=None):
        d = daemon(agents or [fake_acp_agent("a"), fake_acp_agent("b")],
                   gateways=[{"type": "telegram", "token_file": str(token), "api_url": api.url,
                              "allowed_chats": list(allowed)}])
        d.spawn("gateways")
        api.wait_for(lambda t: any(m == "setMyCommands" for m, _ in t.calls))
        return d

    yield api, start
    api.close()


def test_conversation_with_live_answer(telegram):
    api, start = telegram
    start()
    api.user_says(ME, "hello")
    mid = api.wait_for(lambda t: t.find(ME, "echo 1: hello"))
    msg = api.messages[mid]
    api.wait_for(lambda t: not t.messages[mid]["text"].endswith("…"))   # final edit, no ellipsis
    assert msg["text"].startswith("⚙ ")                                  # tool line first
    api.user_says(ME, "again")
    api.wait_for(lambda t: t.find(ME, "echo 2: again"))                  # same conversation


def test_unauthorized_chat_is_ignored(telegram):
    api, start = telegram
    start()
    api.user_says(STRANGER, "hello")
    api.user_says(ME, "ping")
    api.wait_for(lambda t: t.find(ME, "echo 1: ping"))                   # the stranger did not start a turn
    assert api.texts(STRANGER) == []


def test_setup_mode_tells_the_chat_id(telegram):
    api, start = telegram
    d = start(allowed=())
    api.user_says(ME, "hello")
    api.wait_for(lambda t: t.find(ME, str(ME)))
    with d.client() as c:
        assert c.state()["history"] == []                                # nothing reached the agent


@pytest.mark.parametrize("button, outcome", [("✅", "allowed"), ("❌", "denied")])
def test_permission_buttons(telegram, button, outcome):
    api, start = telegram
    start()
    api.user_says(ME, "permission please")
    mid = api.wait_for(lambda t: t.find(ME, "Run rm -rf /tmp/x"))
    data = next(d for label, d in api.messages[mid]["buttons"] if label.startswith(button))
    api.user_presses(ME, mid, data)
    api.wait_for(lambda t: t.find(ME, outcome))
    api.wait_for(lambda t: "→" in t.messages[mid]["text"] and not t.messages[mid]["buttons"])


def test_permission_from_a_panel_turn_reaches_the_chat(telegram):
    api, start = telegram
    d = start()
    with d.client() as panel:
        panel.op("send", text="permission from the desk")
        mid = api.wait_for(lambda t: t.find(ME, "Run rm -rf /tmp/x"))
        data = next(d for label, d in api.messages[mid]["buttons"] if label.startswith("✅"))
        api.user_presses(ME, mid, data)
        while (ev := panel.event(10)) and not (ev["ev"] == "busy" and ev["value"] is False):
            pass
    assert api.find(ME, "allowed") is None             # the answer itself stays on the desk
    with d.client() as c:
        assert c.state()["history"][-1]["text"] == "allowed"


def test_commands(telegram):
    api, start = telegram
    d = start()
    api.user_says(ME, "/agent")
    mid = api.wait_for(lambda t: t.find(ME, "Fake"))
    assert [d_ for _, d_ in api.messages[mid]["buttons"]] == ["a:a", "a:b"]
    api.user_presses(ME, mid, "a:b")
    api.wait_for(lambda t: "→" not in t.messages[mid]["text"] and t.messages[mid]["text"] != t.messages[mid]["history"][0])
    with d.client() as c:
        assert c.state()["active"] == "b"
    api.user_says(ME, "first")
    api.wait_for(lambda t: t.find(ME, "echo 1: first"))
    api.user_says(ME, "/new")
    api.wait_for(lambda t: t.find(ME, "New conversation"))
    api.user_says(ME, "/status")
    api.wait_for(lambda t: t.find(ME, "Messages in the conversation: 0"))
    api.user_says(ME, "/stop")
    api.wait_for(lambda t: t.find(ME, "Nothing to stop"))


def test_stop_and_busy(telegram):
    api, start = telegram
    start()
    api.user_says(ME, "slow job")
    api.wait_for(lambda t: t.find(ME, "working"))
    api.user_says(ME, "another")
    api.wait_for(lambda t: t.find(ME, "Still working"))
    api.user_says(ME, "/stop")
    api.wait_for(lambda t: t.find(ME, "Cancelled"))


def test_markdown_is_sent_as_html(telegram):
    api, start = telegram
    start()
    api.user_says(ME, "**bold** and `a<b`")
    mid = api.wait_for(lambda t: t.find(ME, "echo 1: bold and a<b"))
    api.wait_for(lambda t: "<b>bold</b> and <code>a&lt;b</code>" in t.messages[mid]["markup"])


def test_rejected_markup_falls_back_to_plain_text(telegram):
    api, start = telegram
    api.reject_html = True
    start()
    api.user_says(ME, "**bold**")
    mid = api.wait_for(lambda t: t.find(ME, "echo 1: **bold**"))
    assert api.messages[mid]["markup"] is None


@pytest.mark.parametrize("md, html", [
    ("plain text.", "plain text."),
    ("a < b & c > d", "a &lt; b &amp; c &gt; d"),
    ("**bold**, *italic*, _it_, __b__, ~~gone~~", "<b>bold</b>, <i>italic</i>, <i>it</i>, <b>b</b>, <s>gone</s>"),
    ("snake_case_name and 2*3*4", "snake_case_name and 2*3*4"),
    ("`**not bold** <x>`", "<code>**not bold** &lt;x&gt;</code>"),
    ("[docs](https://x.org/a?b=1&c=2)", '<a href="https://x.org/a?b=1&amp;c=2">docs</a>'),
    ("## Title\n- one\n  * two", "<b>Title</b>\n• one\n  • two"),
    ("> quoted\n> **line**\nafter", "<blockquote>quoted\n<b>line</b></blockquote>\nafter"),
    ("| a | b |\n|---|---|\n| <1 | 2 |", "<pre>| a | b |\n|---|---|\n| &lt;1 | 2 |</pre>"),
    ("x\n```python\nif a < b:\n    pass\n```\ny", 'x\n<pre><code class="language-python">if a &lt; b:\n    pass</code></pre>\ny'),
    ("partial **bold and\n```sh\nls -l …", 'partial **bold and\n<pre><code class="language-sh">ls -l …</code></pre>'),
    ("---", "──────────"),
])
def test_to_html(md, html):
    assert to_html(md) == html
    render_html(html)                                                    # valid for Telegram


JPEG = b"\xff\xd8\xff\xe0fake jpeg"


def test_photo_reaches_the_agent_as_a_file(telegram):
    api, start = telegram
    d = start()
    api.user_sends_photo(ME, JPEG, caption="what is this?")
    mid = api.wait_for(lambda t: t.find(ME, "Attached images"))
    path = api.messages[mid]["text"].split("\n")[-1].removesuffix(" …")
    assert api.messages[mid]["text"].startswith("⚙ ") and "echo 1: what is this?" in api.messages[mid]["text"]
    assert path.startswith(str(d.tmp / "state" / "oring" / "inbox")) and open(path, "rb").read() == JPEG
    with d.client() as c:
        assert c.state()["history"][0]["text"] == f"what is this?\n📎 {path.rsplit('/', 1)[1]}"


def test_photo_as_image_block(telegram, tmp_path):
    api, start = telegram
    log = tmp_path / "prompts.log"
    start(agents=[fake_acp_agent("a", env={"FAKE_ACP_IMAGES": "1", "FAKE_ACP_LOG": str(log)})])
    api.user_sends_photo(ME, JPEG)                                       # no caption
    api.wait_for(lambda t: t.find(ME, "echo 1:"))
    assert log.read_text().splitlines()[-1].endswith(f'"", "<image image/jpeg {len(JPEG)}>"]')


def test_photo_from_unauthorized_chat_is_not_downloaded(telegram):
    api, start = telegram
    start()
    api.user_sends_photo(STRANGER, JPEG)
    api.user_says(ME, "ping")
    api.wait_for(lambda t: t.find(ME, "echo 1: ping"))
    assert not any(m == "getFile" for m, _ in api.calls)


def test_failed_download_is_reported(telegram):
    api, start = telegram
    start()
    api.user_sends_photo(ME, None)                                       # getFile fails
    api.wait_for(lambda t: t.find(ME, "Could not download"))
    api.user_says(ME, "ping")
    api.wait_for(lambda t: t.find(ME, "echo 1: ping"))                   # the chat is not stuck
