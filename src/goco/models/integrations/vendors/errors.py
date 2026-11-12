"""Safe error codes shared by API collectors."""

class CollectionError(Exception):
    def __init__(self, reason, outcome="failed"):
        super().__init__(reason)
        self.reason = reason
        self.outcome = outcome
