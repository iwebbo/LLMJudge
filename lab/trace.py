import sys
import time

JUDGE_MARKER = "rigorous verification auditor"


class TraceState:
    def __init__(self, show_text):
        self.show_text = show_text
        self.calls = []


class _TracedProvider:
    def __init__(self, inner, state):
        self._inner = inner
        self._state = state

    def __getattr__(self, name):  # test_connection, get_available_models, config…
        return getattr(self._inner, name)

    async def stream_completion(self, messages, model, temperature=0.7, **kwargs):
        started = time.monotonic()
        parts = []
        try:
            async for chunk in self._inner.stream_completion(
                messages=messages, model=model, temperature=temperature, **kwargs
            ):
                parts.append(chunk)
                yield chunk
        finally:
            kind = "judge" if any(JUDGE_MARKER in str(m.get("content", "")) for m in messages) else "generator"
            call = {
                "n": len(self._state.calls) + 1, "kind": kind, "model": model, "temperature": temperature,
                "ms": int((time.monotonic() - started) * 1000), "messages": messages, "output": "".join(parts),
            }
            self._state.calls.append(call)
            _print_call(call, self._state.show_text)


def _print_call(call, show_text):
    out = sys.stderr
    out.write(
        f"\n──── LLM call #{call['n']} [{call['kind']}] model={call['model']} temperature={call['temperature']} "
        f"{call['ms']}ms ────\n"
    )
    if show_text:
        for message in call["messages"]:
            out.write(f"[{message['role']}]\n{message['content']}\n\n")
        out.write(f"[output]\n{call['output']}\n")
    else:
        out.write(
            f"{len(call['messages'])} message(s), prompt={sum(len(str(m['content'])) for m in call['messages'])} chars, "
            f"output={len(call['output'])} chars\n"
        )
    out.flush()


def install(show_text=True):
    from app.services.provider_factory import ProviderFactory

    state = TraceState(show_text)
    original = ProviderFactory.create_provider

    def traced(*args, **kwargs):
        return _TracedProvider(original(*args, **kwargs), state)

    ProviderFactory.create_provider = staticmethod(traced)
    return state
