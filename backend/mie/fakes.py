"""Test doubles: FakeClock + FakeMidiOut (plan §11 Phase 1 tests need no hardware)."""

from __future__ import annotations


class FakeClock:
    def __init__(self, t: float = 0.0):
        self.t = float(t)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


class FakeMidiOut:
    """Records every (t, port, message) the engine sends."""

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.sent: list[tuple[float, str, object]] = []

    def __call__(self, port: str, msg) -> None:
        self.sent.append((self.clock(), port, msg))

    def notes(self, kind: str = "note_on", ch: int | None = None, port: str | None = None):
        out = []
        for t, p, m in self.sent:
            if m.type != kind or (kind == "note_on" and m.velocity == 0):
                continue
            if ch is not None and m.channel + 1 != ch:
                continue
            if port is not None and p != port:
                continue
            out.append((t, m.channel + 1, m.note, m.velocity))
        return out

    def clear(self) -> None:
        self.sent.clear()
