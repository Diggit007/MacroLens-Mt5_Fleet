import unittest
from backend.services.event_analyzer import EventAnalyzer

class TestEventAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = EventAnalyzer()

    def test_z_score_calculation(self):
        z = self.analyzer.calculate_z_score(current_deviation=0.2, historical_std=0.1)
        self.assertEqual(z, 2.0)
        
        z = self.analyzer.calculate_z_score(current_deviation=-0.3, historical_std=0.1)
        self.assertEqual(z, -3.0)
        
        z = self.analyzer.calculate_z_score(current_deviation=0.0, historical_std=0.1)
        self.assertEqual(z, 0.0)

    def test_classify_outcome(self):
        res = self.analyzer.classify_outcome(forecast=2.0, actual=2.5, previous=1.9)
        self.assertEqual(res["category"], "BIG_BEAT")
        self.assertEqual(res["deviation"], 0.5)
        
        res = self.analyzer.classify_outcome(forecast=2.0, actual=2.11, previous=1.9)
        self.assertEqual(res["category"], "SMALL_BEAT")
        
        res = self.analyzer.classify_outcome(forecast=2.0, actual=2.05, previous=1.9)
        self.assertEqual(res["category"], "IN_LINE")

    def test_recommendation_logic(self):
        rec = self.analyzer._get_recommendation("BIG_BEAT", 2.0)
        self.assertIn("ENTER", rec)
        
        rec = self.analyzer._get_recommendation("BIG_BEAT", 0.5)
        self.assertIn("WAIT", rec)

if __name__ == '__main__':
    unittest.main()
