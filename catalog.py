"""The fixed 25 Easy / 75 Medium / 25 Hard quest library.

Only metadata and public problem links are stored, not problem statements.
"""

from __future__ import annotations

POOLS = {
    "Easy": (
        "two-sum", "valid-parentheses", "merge-two-sorted-lists",
        "best-time-to-buy-and-sell-stock", "valid-palindrome", "invert-binary-tree",
        "valid-anagram", "binary-search", "flood-fill", "maximum-depth-of-binary-tree",
        "linked-list-cycle", "balanced-binary-tree", "intersection-of-two-linked-lists",
        "reverse-linked-list", "majority-element", "missing-number", "climbing-stairs",
        "diameter-of-binary-tree", "contains-duplicate", "move-zeroes", "single-number",
        "plus-one", "sqrtx", "palindrome-number", "happy-number",
    ),
    "Medium": (
        "add-two-numbers", "longest-substring-without-repeating-characters",
        "longest-palindromic-substring", "zigzag-conversion", "string-to-integer-atoi",
        "container-with-most-water", "3sum", "3sum-closest",
        "letter-combinations-of-a-phone-number", "remove-nth-node-from-end-of-list",
        "generate-parentheses", "swap-nodes-in-pairs", "divide-two-integers",
        "next-permutation", "search-in-rotated-sorted-array",
        "find-first-and-last-position-of-element-in-sorted-array", "valid-sudoku",
        "count-and-say", "combination-sum", "combination-sum-ii", "multiply-strings",
        "jump-game-ii", "permutations", "permutations-ii", "rotate-image",
        "group-anagrams", "powx-n", "maximum-subarray", "spiral-matrix", "jump-game",
        "merge-intervals", "insert-interval", "spiral-matrix-ii", "rotate-list",
        "unique-paths", "unique-paths-ii", "minimum-path-sum", "set-matrix-zeroes",
        "search-a-2d-matrix", "sort-colors", "combinations", "subsets", "word-search",
        "remove-duplicates-from-sorted-array-ii", "search-in-rotated-sorted-array-ii",
        "remove-duplicates-from-sorted-list-ii", "partition-list", "gray-code",
        "subsets-ii", "decode-ways", "reverse-linked-list-ii", "restore-ip-addresses",
        "binary-tree-level-order-traversal", "binary-tree-zigzag-level-order-traversal",
        "construct-binary-tree-from-preorder-and-inorder-traversal",
        "convert-sorted-list-to-binary-search-tree", "validate-binary-search-tree",
        "recover-binary-search-tree", "flatten-binary-tree-to-linked-list", "triangle",
        "best-time-to-buy-and-sell-stock-ii", "best-time-to-buy-and-sell-stock-with-cooldown",
        "palindrome-partitioning", "word-break", "clone-graph", "gas-station",
        "product-of-array-except-self", "number-of-islands", "course-schedule",
        "kth-largest-element-in-an-array", "top-k-frequent-elements", "coin-change",
        "longest-increasing-subsequence", "house-robber", "house-robber-ii",
    ),
    "Hard": (
        "median-of-two-sorted-arrays", "regular-expression-matching",
        "merge-k-sorted-lists", "reverse-nodes-in-k-group",
        "substring-with-concatenation-of-all-words", "longest-valid-parentheses",
        "sudoku-solver", "first-missing-positive", "trapping-rain-water",
        "wildcard-matching", "n-queens", "n-queens-ii", "minimum-window-substring",
        "largest-rectangle-in-histogram", "maximal-rectangle", "scramble-string",
        "distinct-subsequences", "word-ladder", "candy", "word-break-ii",
        "max-points-on-a-line", "find-median-from-data-stream",
        "serialize-and-deserialize-binary-tree", "sliding-window-maximum", "burst-balloons",
    ),
}

DIFFICULTY = {slug: difficulty for difficulty, slugs in POOLS.items() for slug in slugs}
TITLE_OVERRIDES = {"sqrtx": "Sqrt(x)", "powx-n": "Pow(x, n)", "3sum": "3Sum", "3sum-closest": "3Sum Closest"}


def title(slug: str) -> str:
    return TITLE_OVERRIDES.get(slug, slug.replace("-", " ").title())


def resolve_difficulty(slug: str, requested: str | None = None) -> str:
    if requested is not None and requested not in POOLS:
        raise ValueError("Choose Easy, Medium, or Hard.")
    known = DIFFICULTY.get(slug)
    if known and requested and known != requested:
        raise ValueError(f"{title(slug)} is {known} in the quest library.")
    return known or requested or "Easy"
