"""UI run gate: STOP can unlock Run while the agent worker is still blocked."""

from __future__ import annotations

from dataclasses import dataclass

# If the worker is still alive this long after STOP, abandon the run so Run works again.
STOP_ABANDON_SECONDS = 2.5


@dataclass
class RunGate:
    """Generation counter so a late `_finished` from an abandoned worker is ignored."""

    running: bool = False
    token: int = 0

    def begin(self) -> int:
        self.token += 1
        self.running = True
        return self.token

    def accept_finish(self, token: int) -> bool:
        """True if this worker still owns the live run."""
        return self.running and token == self.token

    def finish(self, token: int) -> bool:
        if not self.accept_finish(token):
            return False
        self.running = False
        return True

    def abandon_stuck(self, worker_alive: bool) -> bool:
        """Clear running and bump token so a blocked observe cannot hold Run forever."""
        if not self.running or not worker_alive:
            return False
        self.token += 1
        self.running = False
        return True
