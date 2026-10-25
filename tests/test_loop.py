"""Loop, model layer, handoff, run log and skills, driven by a scripted fake model.

The fake model sits behind httpx.MockTransport, so the real request/parse/fallback
code runs; only the network is replaced.
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from harness.eyes import Eyes
from harness.handoff import Handoff
from harness.hands import BrowserHand, Hands
from harness.llm import ModelClient, ModelUnavailable, Provider, parse_json
from harness.loop import Agent, _wait_until
from harness.skills import Skill, SkillLibrary

FIXTURE = (Path(__file__).parent / "fixtures" / "form.html").resolve().as_uri()


def ok_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


class Script:
    """A fake model: returns queued decisions and records every request."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        d = self.decisions.pop(0) if self.decisions else {"actions": [{"tool": "done", "args": {"summary": "x", "success": False}}]}
        return ok_reply(d if isinstance(d, str) else json.dumps(d))


def client(script, *names) -> ModelClient:
    providers = [Provider(n, f"https://{n}.test/v1", "m", None) for n in (names or ("fake",))]
    return ModelClient(providers, transport=httpx.MockTransport(script))


class FakeChannel:
    name = "fake"

    def __init__(self, answers):
        self.answers, self.sent = list(answers), []

    def send(self, text, image=None, urgent=False):
        self.sent.append(text)

    def start_listening(self):
        pass

    def poll(self):
        return self.answers.pop(0) if self.answers else None


@pytest.fixture
def browser(tmp_path):
    b = BrowserHand(profile_dir=tmp_path / "profile", headless=True, downloads_dir=tmp_path / "dl")
    yield b
    b.close()


def make_agent(tmp_path, browser, script, answers=(), **kw):
    channel = FakeChannel(answers)
    agent = Agent(Hands(browser=browser), Eyes(), client(script), Handoff([channel]),
                  SkillLibrary(tmp_path / "skills"), runs_dir=tmp_path / "runs", **kw)
    return agent, channel


# ---------------- model layer ----------------

def test_parse_json_tolerates_wrappers():
    assert parse_json('<think>hmm {no}</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! {"a": {"b": 2}} hope that helps') == {"a": {"b": 2}}


def test_rate_limited_provider_falls_back_to_next():
    def handler(request):
        if request.url.host == "first.test":
            return httpx.Response(429, headers={"retry-after": "120"}, text="slow down")
        return ok_reply('{"ok": true}')

    c = ModelClient([Provider("first", "https://first.test/v1", "m"), Provider("second", "https://second.test/v1", "m")],
                    transport=httpx.MockTransport(handler))
    assert c.complete([{"role": "user", "content": "hi"}]).provider == "second"
    assert c.providers[0].cooldown_until > time.time() + 100      # first is skipped for a while
    assert c.complete([{"role": "user", "content": "hi"}]).provider == "second"


def test_json_mode_unsupported_is_retried_without_it():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append("response_format" in body)
        if "response_format" in body:
            return httpx.Response(400, text='{"error": "response_format is not supported"}')
        return ok_reply('{"ok": true}')

    c = ModelClient([Provider("p", "https://p.test/v1", "m")], transport=httpx.MockTransport(handler))
    assert c.complete([{"role": "user", "content": "hi"}]).text == '{"ok": true}'
    assert seen == [True, False]


def test_all_providers_down_raises():
    c = ModelClient([Provider("p", "https://p.test/v1", "m")], retries=0,
                    transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad key")))
    with pytest.raises(ModelUnavailable, match="401"):
        c.complete([{"role": "user", "content": "hi"}])


# ---------------- loop ----------------

def test_loop_completes_browser_task_and_logs_it(tmp_path, browser, monkeypatch):
    monkeypatch.setenv("TEST_PASSWORD", "s3cr3t-pw")
    (tmp_path / "skills" / "demo").mkdir(parents=True)
    (tmp_path / "skills" / "demo" / "SKILL.md").write_text(
        '---\nname: demo\ndescription: fixture form demo\nsecrets: ["TEST_PASSWORD"]\n---\nFill the form.\n')
    script = Script([
        {"thought": "open the page", "actions": [{"tool": "browser_goto", "args": {"url": FIXTURE}}], "expect": "form"},
        {"verify": "form visible", "thought": "fill and search", "look": "text", "actions": [
            {"tool": "browser_type", "args": {"ref": 1, "text": "ROORKEE"}},
            {"tool": "browser_type", "args": {"ref": 2, "text": "{{secret:TEST_PASSWORD}}"}},
            {"tool": "browser_select", "args": {"ref": 3, "label": "TATKAL"}},
            {"tool": "browser_click", "args": {"ref": 5}},
            {"tool": "browser_wait", "args": {"text": "Found 3 trains"}}]},
        {"verify": "results shown", "actions": [{"tool": "note", "args": {"text": "results take ~1s"}},
                                                {"tool": "done", "args": {"summary": "found trains"}}]},
    ])
    agent, _ = make_agent(tmp_path, browser, script)
    result = agent.run("search the fixture form", skill="demo")
    assert result.success and result.summary == "found trains" and result.steps == 3
    assert "ROORKEE / TQ" in browser.extract(selector="#result").output
    assert browser.page.locator("#pwd").input_value() == "s3cr3t-pw"   # placeholder was filled in

    # the model saw a screenshot, the tool list, and the results of its previous batch
    first, third = script.requests[0]["messages"], script.requests[2]["messages"]
    assert "browser_click(" in first[0]["content"]
    assert any(p["type"] == "image_url" for p in script.requests[1]["messages"][1]["content"])
    assert "browser_wait" in json.dumps(third) and "OK" in json.dumps(third)
    # step 2 asked for look=text, so step 3 had no image
    assert not any(p["type"] == "image_url" for p in third[1]["content"])

    run = result.run_dir
    steps = [json.loads(l) for l in (run / "steps.jsonl").read_text().splitlines()]
    assert [s["index"] for s in steps] == [1, 2, 3] and all(a["ok"] for a in steps[1]["actions"])
    assert (run / "report.html").exists() and json.loads((run / "meta.json").read_text())["outcome"] == "success"
    for f in run.rglob("*"):
        if f.is_file() and f.suffix in (".json", ".jsonl", ".html", ".txt"):
            assert "s3cr3t-pw" not in f.read_text(), f"secret leaked into {f}"
    assert "results take ~1s" in (tmp_path / "skills" / "demo" / "SKILL.md").read_text()  # self-improvement


def test_batch_stops_at_first_failure(tmp_path, browser):
    script = Script([
        {"actions": [{"tool": "browser_goto", "args": {"url": FIXTURE}},
                     {"tool": "browser_click", "args": {"ref": 999}},
                     {"tool": "browser_click", "args": {"ref": 5}}]},
        {"actions": [{"tool": "done", "args": {"summary": "ok"}}]},
    ])
    agent, _ = make_agent(tmp_path, browser, script)
    agent.run("t")
    feedback = script.requests[1]["messages"][1]["content"][0]["text"]
    assert "ref 999 is no longer on the page" in feedback and "ref': 5" not in feedback


def _approval_skill(tmp_path):
    (tmp_path / "skills" / "pay").mkdir(parents=True)
    (tmp_path / "skills" / "pay" / "SKILL.md").write_text(
        '---\nname: pay\ndescription: pay demo\napprove_before: ["Search"]\n---\nClick Search.\n')


def test_approval_gate_blocks_without_yes(tmp_path, browser):
    _approval_skill(tmp_path)
    script = Script([
        {"actions": [{"tool": "browser_goto", "args": {"url": FIXTURE}}]},
        {"actions": [{"tool": "browser_click", "args": {"ref": 5}}]},
        {"actions": [{"tool": "done", "args": {"summary": "refused", "success": False}}]},
    ])
    agent, channel = make_agent(tmp_path, browser, script, answers=["no"])
    result = agent.run("t", skill="pay")
    assert any("APPROVAL NEEDED" in s and "Search" in s for s in channel.sent)
    assert browser.extract(selector="#result").output == ""             # the click never happened
    assert "did NOT approve" in script.requests[2]["messages"][1]["content"][0]["text"]
    assert result.handoffs == 1


def test_approval_gate_allows_with_yes(tmp_path, browser):
    _approval_skill(tmp_path)
    script = Script([
        {"actions": [{"tool": "browser_goto", "args": {"url": FIXTURE}}]},
        {"actions": [{"tool": "browser_click", "args": {"ref": 5}}, {"tool": "browser_wait", "args": {"text": "Found"}}]},
        {"actions": [{"tool": "done", "args": {"summary": "ok"}}]},
    ])
    agent, _ = make_agent(tmp_path, browser, script, answers=["Yes"])
    assert agent.run("t", skill="pay").success
    assert "Found 3 trains" in browser.extract(selector="#result").output


def test_no_progress_warns_then_asks_human(tmp_path, browser):
    script = Script([
        {"actions": [{"tool": "browser_goto", "args": {"url": FIXTURE}}]},
        {"actions": [{"tool": "browser_click", "args": {"ref": 999}}]},
        {"actions": [{"tool": "browser_click", "args": {"ref": 999}}]},
        {"actions": [{"tool": "done", "args": {"summary": "ok"}}]},
    ])
    agent, channel = make_agent(tmp_path, browser, script, answers=["try the Search button"], max_stuck=2)
    agent.run("t")
    assert any("I'm stuck" in s for s in channel.sent)
    last_prompt = script.requests[3]["messages"][1]["content"][0]["text"]
    assert "repeated the exact same actions" in last_prompt and "try the Search button" in last_prompt


def test_malformed_reply_is_reasked(tmp_path, browser):
    script = Script(["I think I should click", {"actions": [{"tool": "done", "args": {"summary": "ok"}}]}])
    agent, _ = make_agent(tmp_path, browser, script)
    assert agent.run("t").success
    assert "not one valid JSON object" in json.dumps(script.requests[1]["messages"])


def test_wait_until_is_precise():
    target = datetime.now() + timedelta(seconds=1.5)
    res = _wait_until(target.strftime("%H:%M:%S"))
    late = (datetime.now() - target.replace(microsecond=0)).total_seconds()
    assert res.ok and 0 <= late < 0.15
    assert _wait_until("00:00:00").ok  # already passed: continue immediately


def test_skill_frontmatter_and_matching(tmp_path):
    d = tmp_path / "skills" / "irctc_tatkal"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text('---\nname: irctc_tatkal\ndescription: Book a Tatkal train ticket on IRCTC\n'
                                'approve_before: ["Pay & Book"]  # money moves\nmax_steps: 80\n---\nbody\n')
    lib = SkillLibrary(tmp_path / "skills")
    s = lib.get("irctc_tatkal")
    assert s.approve_before == ["Pay & Book"] and s.meta["max_steps"] == 80 and s.body == "body"
    assert lib.match("book the cheapest tatkal ticket from Roorkee to Haridwar").name == "irctc_tatkal"
    assert lib.match("open the calculator") is None
    s.learn(["lesson one"], "run-1")
    s.learn(["lesson two"], "run-2")
    text = (d / "SKILL.md").read_text()
    assert text.count("## Learned from runs") == 1 and "lesson one" in text and "lesson two" in text
    assert Skill.load(d / "SKILL.md").approve_before == ["Pay & Book"]


def test_timeout_moves_to_next_provider_and_preset_alias():
    def handler(request):
        if request.url.host == "slow.test":
            raise httpx.ReadTimeout("hung")
        return ok_reply('{"ok": true}')

    c = ModelClient([Provider("slow", "https://slow.test/v1", "m"), Provider("fast", "https://fast.test/v1", "m")],
                    transport=httpx.MockTransport(handler))
    assert c.complete([{"role": "user", "content": "hi"}]).provider == "fast"
    assert c.providers[0].cooldown_until > time.time()
    lite = Provider.from_config("gemini_lite", {"preset": "gemini", "model": "x-lite"})
    assert lite.base_url.startswith("https://generativelanguage") and lite.key_env == "GEMINI_API_KEY"
