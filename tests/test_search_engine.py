import unittest
from services.search_engine import normalize_query, calculate_token_similarity


class TestSearchEngine(unittest.TestCase):
    def test_normalize_query(self):
        # English punctuation removal
        self.assertEqual(normalize_query("  Tony Stark... stare?! "), "tony stark stare")
        # Bengali unicode characters preservation
        self.assertEqual(normalize_query("হাহা! দারুণ মিম..."), "হাহা দারুণ মিম")
        # Empty query
        self.assertEqual(normalize_query(""), "")

    def test_token_similarity(self):
        # Exact match
        self.assertEqual(calculate_token_similarity("cat", "cat"), 1.0)

        # Substring containment
        score_contain = calculate_token_similarity("stark", "tony stark rolling eyes")
        self.assertGreaterEqual(score_contain, 0.8)

        # Partial overlap
        score_overlap = calculate_token_similarity("funny cat meme", "cat meme compilation")
        self.assertGreater(score_overlap, 0.4)

        # Completely different
        score_diff = calculate_token_similarity("dog", "airplane")
        self.assertLess(score_diff, 0.2)


if __name__ == "__main__":
    unittest.main()
