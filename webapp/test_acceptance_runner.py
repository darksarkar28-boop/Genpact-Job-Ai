import unittest
from unittest.mock import patch

from .acceptance_runner import AcceptanceRunner


class AcceptanceRunnerTests(unittest.TestCase):
    def test_only_an_executed_passing_suite_counts_as_evidence(self):
        runner = AcceptanceRunner()
        suite = unittest.TestSuite()
        case = unittest.FunctionTestCase(lambda: None)
        result = unittest.TestResult()
        self.assertGreater(runner.suite_result(suite, result), 0)
        result.testsRun = 1
        self.assertEqual(runner.suite_result(suite, result), 0)
        for field in ("skipped", "expectedFailures", "unexpectedSuccesses", "errors", "failures"):
            with self.subTest(field=field), patch.object(result, field, [case]):
                self.assertGreater(runner.suite_result(suite, result), 0)

    def test_empty_selected_label_fails_even_when_other_labels_have_tests(self):
        with patch("django.test.runner.DiscoverRunner.load_tests_for_label", return_value=unittest.TestSuite()):
            with self.assertRaisesRegex(ValueError, "No acceptance tests found for"):
                AcceptanceRunner().load_tests_for_label("example.empty", {})
