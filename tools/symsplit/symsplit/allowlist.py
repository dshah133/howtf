"""The intentional-interposer allowlist (editable policy file)."""
from __future__ import annotations

import os
from typing import List, Optional

_DEFAULT = os.path.join(os.path.dirname(__file__), "data", "allowlist.txt")


class Allowlist:
    def __init__(self, exact, prefixes, source):
        self.exact = exact          # set[str]
        self.prefixes = prefixes    # list[str]
        self.source = source

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Allowlist":
        path = path or _DEFAULT
        exact = set()
        prefixes: List[str] = []
        with open(path) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                if line.endswith("*"):
                    prefixes.append(line[:-1])
                else:
                    exact.add(line)
        return cls(exact, prefixes, path)

    def match(self, name: str) -> Optional[str]:
        """Return the matching pattern if `name` is allowlisted, else None."""
        if name in self.exact:
            return name
        for p in self.prefixes:
            if name.startswith(p):
                return p + "*"
        return None
