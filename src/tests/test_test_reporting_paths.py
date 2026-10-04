"""Developer artifacts stay out of production directories, independently of cwd."""
from pathlib import Path

from tests.html_reporter import HTMLTestReporter
from tests.reporting.history import TestHistoryManager as HistoryStore


def test_report_default_is_scoped_to_tests(tmp_path, monkeypatch):
    import tests.html_reporter as reporter
    module_path = tmp_path / "src/tests/html_reporter.py"
    monkeypatch.setattr(reporter, "__file__", str(module_path))
    monkeypatch.chdir(tmp_path)
    output = HTMLTestReporter().generate_report({
        "overall_stats": {"total_tests": 2, "successful_tests": 1, "failed_tests": 1,
                          "success_rate": 50},
    })
    assert Path(output).parent == tmp_path / "src/tests/results"
    assert Path(output).is_file()
    assert not (tmp_path / "web").exists()


def test_history_default_is_scoped_to_tests(tmp_path, monkeypatch):
    import tests.reporting.history as history
    monkeypatch.setattr(history, "__file__", str(tmp_path / "src/tests/reporting/history.py"))
    monkeypatch.chdir(tmp_path)
    manager = HistoryStore()
    assert Path(manager.db_path) == tmp_path / "src/tests/results/test_history.db"
    assert Path(manager.db_path).is_file()
    assert not (tmp_path / "output").exists()
