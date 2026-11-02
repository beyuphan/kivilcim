"""Union-find: groups items that are linked, directly or through others."""

from collections.abc import Hashable


class Components:
    def __init__(self) -> None:
        self.parent: dict = {}

    def add(self, node: Hashable) -> None:
        self.parent.setdefault(node, node)

    def find(self, node: Hashable):
        root = node
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[node] != root:
            self.parent[node], node = root, self.parent[node]
        return root

    def union(self, a: Hashable, b: Hashable) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra
