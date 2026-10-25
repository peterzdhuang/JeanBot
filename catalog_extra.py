"""Curated public LeetCode practice links, grouped by useful dungeon topics.

These are practice categories (a problem may have several), not an exhaustive
copy of LeetCode's tags. No statements, solutions, or paid content are included.
"""

TOPIC_PROBLEMS = {
    "Two Pointers": {
        "Easy": """valid-palindrome move-zeroes reverse-string reverse-vowels-of-a-string
            remove-duplicates-from-sorted-array remove-element merge-sorted-array
            squares-of-a-sorted-array backspace-string-compare valid-palindrome-ii
            is-subsequence reverse-words-in-a-string-iii sort-array-by-parity
            intersection-of-two-arrays intersection-of-two-arrays-ii
            duplicate-zeros check-if-n-and-its-double-exist""",
        "Medium": """container-with-most-water 3sum 3sum-closest 4sum two-sum-ii-input-array-is-sorted
            sort-colors remove-duplicates-from-sorted-array-ii next-permutation
            reverse-words-in-a-string boats-to-save-people partition-labels
            bag-of-tokens append-characters-to-string-to-make-subsequence
            maximum-number-of-vowels-in-a-substring-of-given-length""",
        "Hard": "trapping-rain-water",
    },
    "Sliding Window": {
        "Easy": """maximum-average-subarray-i contains-duplicate-ii
            longest-nice-substring minimum-recolors-to-get-k-consecutive-black-blocks
            minimum-difference-between-highest-and-lowest-of-k-scores""",
        "Medium": """longest-substring-without-repeating-characters
            longest-repeating-character-replacement permutation-in-string
            find-all-anagrams-in-a-string minimum-size-subarray-sum
            fruit-into-baskets max-consecutive-ones-iii
            longest-subarray-of-1s-after-deleting-one-element
            maximum-number-of-vowels-in-a-substring-of-given-length
            subarray-product-less-than-k binary-subarrays-with-sum
            count-number-of-nice-subarrays maximum-erasure-value
            maximum-sum-of-distinct-subarrays-with-length-k
            frequency-of-the-most-frequent-element get-equal-substrings-within-budget
            grumpy-bookstore-owner take-k-of-each-character-from-left-and-right""",
        "Hard": """minimum-window-substring sliding-window-maximum
            substring-with-concatenation-of-all-words subarrays-with-k-different-integers
            minimum-number-of-k-consecutive-bit-flips count-subarrays-with-fixed-bounds""",
    },
    "Binary Search": {
        "Easy": """binary-search sqrtx search-insert-position first-bad-version
            guess-number-higher-or-lower valid-perfect-square arranging-coins
            find-smallest-letter-greater-than-target""",
        "Medium": """search-in-rotated-sorted-array search-in-rotated-sorted-array-ii
            find-first-and-last-position-of-element-in-sorted-array search-a-2d-matrix
            search-a-2d-matrix-ii find-minimum-in-rotated-sorted-array find-peak-element
            koko-eating-bananas capacity-to-ship-packages-within-d-days
            successful-pairs-of-spells-and-potions single-element-in-a-sorted-array
            time-based-key-value-store h-index-ii magnetic-force-between-two-balls
            minimum-number-of-days-to-make-m-bouquets peak-index-in-a-mountain-array""",
        "Hard": """median-of-two-sorted-arrays split-array-largest-sum
            find-minimum-in-rotated-sorted-array-ii find-in-mountain-array
            kth-smallest-number-in-multiplication-table""",
    },
    "Arrays & Hashing": {
        "Easy": """two-sum contains-duplicate valid-anagram majority-element missing-number
            plus-one happy-number roman-to-integer longest-common-prefix
            contains-duplicate-ii ransom-note isomorphic-strings word-pattern
            first-unique-character-in-a-string find-the-difference
            longest-palindrome pascals-triangle pascals-triangle-ii
            find-all-numbers-disappeared-in-an-array third-maximum-number
            max-consecutive-ones reshape-the-matrix set-mismatch
            degree-of-an-array find-pivot-index monotonic-array
            unique-number-of-occurrences number-of-good-pairs
            running-sum-of-1d-array kids-with-the-greatest-number-of-candies
            richest-customer-wealth shuffle-the-array build-array-from-permutation
            concatenation-of-array difference-between-element-sum-and-digit-sum-of-an-array""",
        "Medium": """group-anagrams product-of-array-except-self valid-sudoku set-matrix-zeroes
            rotate-image spiral-matrix spiral-matrix-ii rotate-array subarray-sum-equals-k
            continuous-subarray-sum longest-consecutive-sequence h-index
            insert-delete-getrandom-o1 find-all-duplicates-in-an-array
            majority-element-ii range-sum-query-2d-immutable sort-an-array
            equal-row-and-column-pairs determine-if-two-strings-are-close
            find-the-winner-of-an-array-game brick-wall""",
        "Hard": "first-missing-positive max-points-on-a-line",
    },
    "Linked Lists": {
        "Easy": """merge-two-sorted-lists linked-list-cycle intersection-of-two-linked-lists
            reverse-linked-list palindrome-linked-list middle-of-the-linked-list
            remove-linked-list-elements remove-duplicates-from-sorted-list
            convert-binary-number-in-a-linked-list-to-integer""",
        "Medium": """add-two-numbers remove-nth-node-from-end-of-list swap-nodes-in-pairs
            rotate-list remove-duplicates-from-sorted-list-ii partition-list reverse-linked-list-ii
            linked-list-cycle-ii reorder-list copy-list-with-random-pointer
            odd-even-linked-list sort-list insertion-sort-list add-two-numbers-ii
            delete-node-in-a-linked-list lru-cache design-linked-list
            delete-the-middle-node-of-a-linked-list maximum-twin-sum-of-a-linked-list""",
        "Hard": "merge-k-sorted-lists reverse-nodes-in-k-group",
    },
    "Stacks": {
        "Easy": """valid-parentheses baseball-game implement-queue-using-stacks
            implement-stack-using-queues next-greater-element-i remove-all-adjacent-duplicates-in-string
            make-the-string-great final-prices-with-a-special-discount-in-a-shop""",
        "Medium": """min-stack evaluate-reverse-polish-notation daily-temperatures
            asteroid-collision decode-string simplify-path remove-k-digits
            next-greater-element-ii online-stock-span car-fleet
            sum-of-subarray-minimums validate-stack-sequences
            remove-all-adjacent-duplicates-in-string-ii minimum-remove-to-make-valid-parentheses
            basic-calculator-ii removing-stars-from-a-string""",
        "Hard": """largest-rectangle-in-histogram maximal-rectangle longest-valid-parentheses
            basic-calculator""",
    },
    "Trees": {
        "Easy": """invert-binary-tree maximum-depth-of-binary-tree balanced-binary-tree
            diameter-of-binary-tree same-tree symmetric-tree path-sum
            minimum-depth-of-binary-tree binary-tree-preorder-traversal
            binary-tree-inorder-traversal binary-tree-postorder-traversal
            convert-sorted-array-to-binary-search-tree merge-two-binary-trees
            search-in-a-binary-search-tree range-sum-of-bst leaf-similar-trees
            sum-of-left-leaves binary-tree-paths subtree-of-another-tree""",
        "Medium": """binary-tree-level-order-traversal binary-tree-zigzag-level-order-traversal
            construct-binary-tree-from-preorder-and-inorder-traversal
            convert-sorted-list-to-binary-search-tree validate-binary-search-tree
            recover-binary-search-tree flatten-binary-tree-to-linked-list
            binary-tree-right-side-view count-good-nodes-in-binary-tree
            kth-smallest-element-in-a-bst lowest-common-ancestor-of-a-binary-search-tree
            lowest-common-ancestor-of-a-binary-tree path-sum-ii path-sum-iii
            sum-root-to-leaf-numbers construct-binary-tree-from-inorder-and-postorder-traversal
            binary-search-tree-iterator delete-node-in-a-bst
            maximum-level-sum-of-a-binary-tree longest-zigzag-path-in-a-binary-tree
            all-nodes-distance-k-in-binary-tree find-duplicate-subtrees""",
        "Hard": """serialize-and-deserialize-binary-tree binary-tree-maximum-path-sum
            binary-tree-cameras recover-a-tree-from-preorder-traversal""",
    },
    "Graphs": {
        "Easy": """flood-fill island-perimeter find-the-town-judge
            find-if-path-exists-in-graph find-center-of-star-graph""",
        "Medium": """clone-graph number-of-islands course-schedule course-schedule-ii
            rotting-oranges max-area-of-island pacific-atlantic-water-flow
            surrounded-regions number-of-provinces redundant-connection
            evaluate-division keys-and-rooms is-graph-bipartite
            possible-bipartition all-paths-from-source-to-target
            shortest-path-in-binary-matrix nearest-exit-from-entrance-in-maze
            network-delay-time cheapest-flights-within-k-stops
            min-cost-to-connect-all-points accounts-merge
            minimum-height-trees snakes-and-ladders open-the-lock
            01-matrix""",
        "Hard": """word-ladder word-ladder-ii reconstruct-itinerary
            swim-in-rising-water longest-increasing-path-in-a-matrix
            critical-connections-in-a-network remove-max-number-of-edges-to-keep-graph-fully-traversable""",
    },
    "Dynamic Programming": {
        "Easy": """climbing-stairs min-cost-climbing-stairs fibonacci-number
            n-th-tribonacci-number counting-bits divisor-game""",
        "Medium": """maximum-subarray unique-paths unique-paths-ii minimum-path-sum decode-ways
            triangle best-time-to-buy-and-sell-stock-with-cooldown word-break coin-change
            longest-increasing-subsequence house-robber house-robber-ii
            coin-change-ii partition-equal-subset-sum target-sum
            longest-common-subsequence longest-palindromic-subsequence
            maximum-product-subarray perfect-squares integer-break
            combination-sum-iv delete-and-earn unique-binary-search-trees
            unique-binary-search-trees-ii interleaving-string edit-distance
            best-time-to-buy-and-sell-stock-with-transaction-fee
            minimum-falling-path-sum number-of-dice-rolls-with-target-sum
            domino-and-tromino-tiling uncrossed-lines maximal-square""",
        "Hard": """regular-expression-matching wildcard-matching distinct-subsequences
            burst-balloons scramble-string word-break-ii palindrome-partitioning-ii
            best-time-to-buy-and-sell-stock-iii best-time-to-buy-and-sell-stock-iv
            student-attendance-record-ii frog-jump russian-doll-envelopes
            minimum-cost-to-cut-a-stick count-vowels-permutation""",
    },
    "Backtracking": {
        "Medium": """letter-combinations-of-a-phone-number generate-parentheses
            combination-sum combination-sum-ii permutations permutations-ii
            combinations subsets subsets-ii word-search palindrome-partitioning
            restore-ip-addresses combination-sum-iii beautiful-arrangement
            letter-case-permutation split-a-string-into-the-max-number-of-unique-substrings
            matchsticks-to-square""",
        "Hard": "sudoku-solver n-queens n-queens-ii word-search-ii expression-add-operators",
    },
    "Heaps": {
        "Easy": "kth-largest-element-in-a-stream last-stone-weight relative-ranks",
        "Medium": """kth-largest-element-in-an-array top-k-frequent-elements k-closest-points-to-origin
            top-k-frequent-words task-scheduler reorganize-string
            kth-smallest-element-in-a-sorted-matrix find-k-pairs-with-smallest-sums
            seat-reservation-manager smallest-number-in-infinite-set
            maximum-subsequence-score total-cost-to-hire-k-workers furthest-building-you-can-reach""",
        "Hard": """find-median-from-data-stream merge-k-sorted-lists ipo
            smallest-range-covering-elements-from-k-lists minimum-cost-to-hire-k-workers""",
    },
    "Greedy": {
        "Easy": """best-time-to-buy-and-sell-stock assign-cookies lemonade-change
            can-place-flowers maximum-units-on-a-truck""",
        "Medium": """jump-game jump-game-ii gas-station best-time-to-buy-and-sell-stock-ii
            partition-labels boats-to-save-people valid-parenthesis-string
            wiggle-subsequence increasing-triplet-subsequence hand-of-straights
            minimum-number-of-arrows-to-burst-balloons""",
        "Hard": "candy patching-array minimum-number-of-refueling-stops",
    },
    "Intervals": {
        "Easy": "summary-ranges",
        "Medium": """merge-intervals insert-interval non-overlapping-intervals
            minimum-number-of-arrows-to-burst-balloons interval-list-intersections
            my-calendar-i my-calendar-ii remove-covered-intervals""",
        "Hard": """minimum-interval-to-include-each-query data-stream-as-disjoint-intervals
            my-calendar-iii the-skyline-problem""",
    },
    "Bit Manipulation": {
        "Easy": """single-number missing-number counting-bits number-of-1-bits reverse-bits
            power-of-two power-of-four hamming-distance number-complement
            binary-number-with-alternating-bits xor-operation-in-an-array""",
        "Medium": """single-number-ii single-number-iii sum-of-two-integers
            bitwise-and-of-numbers-range maximum-xor-of-two-numbers-in-an-array
            gray-code minimum-flips-to-make-a-or-b-equal-to-c""",
        "Hard": "minimum-one-bit-operations-to-make-integers-zero",
    },
    "Strings & Math": {
        "Easy": """palindrome-number add-binary add-strings excel-sheet-column-number
            excel-sheet-column-title fizz-buzz power-of-three
            perfect-number detect-capital length-of-last-word""",
        "Medium": """longest-palindromic-substring zigzag-conversion string-to-integer-atoi
            divide-two-integers count-and-say multiply-strings powx-n integer-to-roman
            reverse-integer string-compression compare-version-numbers
            fraction-to-recurring-decimal count-primes factorial-trailing-zeroes
            evaluate-reverse-polish-notation""",
        "Hard": "integer-to-english-words number-of-digit-one",
    },
    "Tries": {
        "Easy": "longest-common-prefix",
        "Medium": """implement-trie-prefix-tree design-add-and-search-words-data-structure
            replace-words search-suggestions-system map-sum-pairs
            maximum-xor-of-two-numbers-in-an-array""",
        "Hard": "word-search-ii palindrome-pairs concatenated-words",
    },
}
