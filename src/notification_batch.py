"""Pure batching shared by the resident's blocking notice paths."""


class BlockingMessage(str):
    """A blocking notice, routed as a string but merged by the notification sink."""
    force = False


class AdvanceWarning(BlockingMessage):
    """The prediction, episode and occurrence travel with the display text."""

    def __new__(cls, text, *, kind, episode, occurrence, triggers, in_use, timing):
        warning = super().__new__(cls, text)
        warning.kind = kind
        warning.episode = episode
        warning.occurrence = occurrence
        warning.triggers = triggers
        warning.in_use = in_use
        warning.timing = timing
        return warning


class NotificationBatch:
    """Keep all detail in a ten-second burst; suppress identical lines for ten minutes.

    The caller posts the result with one stable notification id. Time is monotonic seconds.
    Changed warnings (five minutes -> one minute) are different messages and still get through.
    """
    def __init__(self):
        self.seen = {}
        self.lines = {}
        self.last = float("-inf")

    def merge(self, messages, now, *, keep_burst=True):
        self.seen = {key: at for key, at in self.seen.items() if now - at < 600}
        fresh = {}
        for message in messages:
            for line in message.splitlines():
                line = line.strip()
                if not line:
                    continue
                key = (message.episode, message.occurrence, line) if isinstance(message, AdvanceWarning) else line
                if key not in self.seen:
                    self.seen[key] = now
                    fresh[line] = None
        if not fresh:
            return None
        if not keep_burst or now - self.last > 10:
            self.lines.clear()
            self.last = now
        self.lines.update(fresh)
        return "\n".join(self.lines)
