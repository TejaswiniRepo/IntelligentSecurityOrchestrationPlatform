"""Provider adapters against mocked HTTP transports (no real network)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from isop.config import Settings, load_dotenv
from isop.llm import ConfigurationError, LlmError, OllamaProvider, build_llm_provider
from isop.llm.huggingface import HuggingFaceProvider

pytestmark = pytest.mark.anyio
SCHEMA = {"type": "object"}


def ollama(handler) -> OllamaProvider:
    return OllamaProvider("http://127.0.0.1:11434", "qwen2.5:3b", timeout_s=5, temperature=0.0,
                          transport=httpx.MockTransport(handler))


async def test_ollama_request_shape_and_parse() -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"message": {"content": '{"steps": [], "done": true}'}})

    out = await ollama(handler).complete_json("sys", "user", SCHEMA)
    assert out == {"steps": [], "done": True}
    assert seen["url"] == "http://127.0.0.1:11434/api/chat"
    b = seen["body"]
    assert b["model"] == "qwen2.5:3b" and b["stream"] is False and b["format"] == SCHEMA
    assert b["options"]["temperature"] == 0.0 and "seed" in b["options"]


@pytest.mark.parametrize(
    ("handler", "kind"),
    [
        (lambda r: httpx.Response(404, json={"error": "model not found"}), "model_missing"),
        (lambda r: httpx.Response(500, text="boom"), "http_error"),
        (lambda r: httpx.Response(200, json={"message": {"content": "not json"}}), "malformed"),
        (lambda r: httpx.Response(200, json={"message": {"content": "[1, 2]"}}), "malformed"),
        (lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")), "unavailable"),
        (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")), "timeout"),
    ],
    ids=["404", "500", "not-json", "not-object", "connect-refused", "timeout"],
)
async def test_ollama_errors_are_classified(handler, kind: str) -> None:
    with pytest.raises(LlmError) as e:
        await ollama(handler).complete_json("s", "u", SCHEMA)
    assert e.value.kind == kind


async def test_ollama_health_reports_missing_model_without_pulling() -> None:
    paths = []

    def handler(req: httpx.Request) -> httpx.Response:
        paths.append(req.url.path)
        if req.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.10"})
        return httpx.Response(200, json={"models": [{"name": "llama3.2:1b", "model": "llama3.2:1b"}]})

    h = await ollama(handler).health()
    assert h["reachable"] and not h["model_present"]
    assert "/api/pull" not in paths


async def test_huggingface_request_shape_and_auth() -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"steps": [], "done": true}'}}]})

    p = HuggingFaceProvider("Qwen/Qwen2.5-7B-Instruct", token="hf_test", transport=httpx.MockTransport(handler))
    assert await p.complete_json("s", "u", SCHEMA) == {"steps": [], "done": True}
    assert seen["auth"] == "Bearer hf_test"
    assert seen["body"]["response_format"]["json_schema"]["schema"] == SCHEMA


def test_huggingface_requires_token() -> None:
    with pytest.raises(LlmError):
        HuggingFaceProvider("m", token=None)


# ---------------------------------------------------------------- configuration / offline enforcement


@pytest.mark.parametrize("provider", ["anthropic", "huggingface"])
def test_offline_mode_refuses_cloud_providers(provider: str) -> None:
    with pytest.raises(ConfigurationError, match="needs internet"):
        build_llm_provider(Settings(llm_provider=provider, offline=True))


def test_offline_mode_refuses_remote_ollama() -> None:
    with pytest.raises(ConfigurationError, match="loopback"):
        build_llm_provider(Settings(llm_provider="ollama", ollama_base_url="http://10.0.0.5:11434", offline=True))


def test_deterministic_mode_builds_no_provider() -> None:
    assert build_llm_provider(Settings(llm_provider="deterministic")) is None


def test_default_settings_are_offline_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("LLM_PROVIDER", "ISOP_OFFLINE", "OLLAMA_MODEL", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    s = Settings.from_env(env_file=None)
    assert (s.llm_provider, s.offline, s.ollama_base_url) == ("ollama", True, "http://127.0.0.1:11434")


def test_dotenv_is_read_but_real_env_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text("# comment\nOLLAMA_MODEL=qwen2.5:1.5b\nLLM_PROVIDER='deterministic'\n", encoding="utf-8")
    assert load_dotenv(env)["LLM_PROVIDER"] == "deterministic"
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    s = Settings.from_env(env_file=env)
    assert (s.ollama_model, s.llm_provider) == ("qwen2.5:1.5b", "ollama")


def test_invalid_provider_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        Settings(llm_provider="gpt")


async def test_ollama_timeout_is_a_hard_deadline() -> None:
    import asyncio
    import time

    async def slow(_: httpx.Request) -> httpx.Response:  # a generation that never finishes in time
        await asyncio.sleep(5)
        return httpx.Response(200, json={"message": {"content": "{}"}})

    p = OllamaProvider("http://127.0.0.1:11434", "qwen2.5:3b", timeout_s=0.3, transport=httpx.MockTransport(slow))
    started = time.monotonic()
    with pytest.raises(LlmError) as e:
        await p.complete_json("s", "u", SCHEMA)
    assert e.value.kind == "timeout" and time.monotonic() - started < 1.5


def test_default_model_is_the_official_hugging_face_gguf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    s = Settings.from_env(env_file=None)
    assert s.ollama_model == "hf.co/Qwen/Qwen2.5-3B-Instruct-GGUF:Q4_K_M"
    summary = s.public_summary()
    assert summary["model_source"] == "Hugging Face Hub (run locally)"
    assert s.offline and summary["llm_provider"] == "ollama"  # still local inference, no cloud call


def test_model_source_labels() -> None:
    from isop.config import model_source

    assert model_source("hf.co/Qwen/Qwen2.5-3B-Instruct-GGUF:Q4_K_M").startswith("Hugging Face Hub")
    assert model_source("qwen2.5:3b").startswith("Ollama library")
    assert Settings(llm_provider="deterministic").public_summary()["model_source"] is None


async def test_health_reports_hugging_face_source() -> None:
    hf = "hf.co/Qwen/Qwen2.5-3B-Instruct-GGUF:Q4_K_M"

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.10"})
        return httpx.Response(200, json={"models": [{"name": hf, "model": hf}]})

    h = await OllamaProvider("http://127.0.0.1:11434", hf, transport=httpx.MockTransport(handler)).health()
    assert h["model_present"] and h["model_source"] == "Hugging Face Hub (run locally)"
