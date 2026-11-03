"""Pure batching shared by the resident's blocking notice paths."""


class BlockingMessage(str):
    """A blocking notice, routed as a string but merged by the notification sink."""
    force = False


class NotificationBatch:
    """Keep all detail in a ten-second burst; suppress identical lines for ten minutes.

    The caller posts the result with one stable notification id. Time is monotonic seconds.
    Changed warnings (five minutes -> one minute) are different messages and still get through.
    """
    def __init__(self):
        self.seen = {}
        self.lines = {}
        self.last = float("-inf")

    def merge(self, messages, now):
        self.seen = {text: at for text, at in self.seen.items() if now - at < 600}
        fresh = list(dict.fromkeys(line.strip() for text in messages for line in text.splitlines() if line.strip()))
        fresh = [text for text in fresh if text not in self.seen]
        if not fresh:
            return None
        if now - self.last > 10:
            self.lines.clear()
            self.last = now
        for text in fresh:
            self.seen[text] = now
            self.lines[text] = None
        return "\n".join(self.lines)
