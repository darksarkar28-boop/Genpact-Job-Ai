"""Make issue acceptance runs and their evidence visible in GitHub Actions."""
import os
from pathlib import Path

from django.test.runner import DiscoverRunner
from django.test.utils import iter_test_cases


class AcceptanceRunner(DiscoverRunner):
    def load_tests_for_label(self, label, discover_kwargs):
        suite = super().load_tests_for_label(label, discover_kwargs)
        if suite is None or suite.countTestCases() == 0:
            raise ValueError(f"No acceptance tests found for {label}")
        return suite

    def run_suite(self, suite, **kwargs):
        # The unittest runner releases suite entries as they finish.
        case_ids = [case.id() for case in iter_test_cases(suite)]
        result = super().run_suite(suite, **kwargs)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            rejected = [
                *(case.id() for case, _ in result.failures + result.errors),
                *(case.id() for case, _ in result.skipped + result.expectedFailures),
                *(case.id() for case in result.unexpectedSuccesses),
            ]
            with Path(summary).open("a", encoding="utf-8") as report:
                report.write(f"## Acceptance evidence\n\n{result.testsRun} test cases ran. ")
                if self.suite_result(suite, result):
                    report.write("Review the failures, omissions or skips in the job log.\n")
                else:
                    report.write("Every selected case passed.\n")
                if rejected:
                    report.write("\n### Cases requiring attention\n\n")
                    report.writelines(f"- `{name}`\n" for name in rejected)
                report.write("\n<details><summary>Selected test cases</summary>\n\n")
                report.writelines(f"- `{name}`\n" for name in case_ids)
                report.write("\n</details>\n")
        return result

    def suite_result(self, suite, result, **kwargs):
        failures = super().suite_result(suite, result, **kwargs)
        return failures + len(result.skipped) + len(result.expectedFailures) + int(result.testsRun == 0)
