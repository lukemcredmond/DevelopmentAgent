"""Runaway generation abort in consume_chat_stream."""

from backend.services.llm_provider import (
    ChatResult,
    ProviderMessage,
    RunawayGenerationAborted,
    consume_chat_stream,
)


def _slow_text_stream():
    yield ChatResult(message=ProviderMessage(role="assistant", content="a"), eval_count=1)
    import time

    time.sleep(0.05)
    yield ChatResult(message=ProviderMessage(role="assistant", content="b"), eval_count=2)


def test_consume_chat_stream_aborts_long_text_without_tools():
    try:
        consume_chat_stream(
            _slow_text_stream(),
            empty_timeout_sec=1,
            flowing_timeout_sec=1,
            eval_timeout_sec=0.01,
        )
        assert False, "expected RunawayGenerationAborted"
    except RunawayGenerationAborted:
        pass
