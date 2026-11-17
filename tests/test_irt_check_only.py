import os
from contextlib import contextmanager
from pathlib import Path
import shutil
from threading import Event
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from selenium.common.exceptions import TimeoutException

from automation.artifacts import RunArtifacts
from automation.cancellation import RunCancelled
from automation.irt_duplicate_checker import IRTDuplicateChecker, IRTDuplicateCheckError
from automation.workflow import run_searches
from config.app_settings import RUN_MODE_COLLECTION, RUN_MODE_IRT_CHECK
from utils.directory_input import parse_pdf_directory


MATCH = {"LNI": "EXISTING-LNI", "Received Date": "11/04/2026", "IRT Court Code": "FDUSAP06"}


@contextmanager
def test_directory():
    root = (Path(__file__).resolve().parents[1] / "tmp" / "irt_mode_tests").resolve()
    path = root / uuid4().hex
    path.mkdir(parents=True)
    try:
        yield str(path)
    finally:
        assert path.resolve().is_relative_to(root)
        shutil.rmtree(path)


class IRTCheckOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp_path = self.enterContext(test_directory())
        root = Path(self.temp_path) / "run"
        self.artifacts = RunArtifacts(root, root / "run.log", root / "Report.xlsx", root / "debug")
        self.artifacts.log = Mock()
        self.create = self.enterContext(patch("automation.workflow.RunArtifacts.create", return_value=self.artifacts))
        self.checker_class = self.enterContext(patch("automation.workflow.IRTDuplicateChecker"))
        self.checker = self.checker_class.return_value
        self.courtlink = self.enterContext(patch("automation.workflow.session"))
        self.login = self.enterContext(patch("automation.workflow.LPLogin"))

    def run_check(self, answers, **kwargs):
        self.checker.find_existing_counsel_any.side_effect = answers
        return run_searches(
            [{"court_label": "6th Circuit Court", "output_folder": "USAP06",
              "docket_numbers": "26-1000, 26-1001, 26-1002",
              "docket_output_filenames": {"26-1000": "some_expected_counsel.htm"}}],
            "", "", run_mode=RUN_MODE_IRT_CHECK, progress_callback=lambda *_: None, **kwargs,
        )

    def assert_no_collection(self):
        self.courtlink.assert_not_called()
        self.login.assert_not_called()
        self.checker.quit.assert_called_once()
        self.assertEqual(list(self.artifacts.run_dir.rglob("*.htm")), [])

    def test_mixed_results_stop_after_irt_and_export_every_docket(self):
        result = self.run_check([MATCH, None, None])
        self.assertTrue(result["success"])
        self.assertEqual((result["checked"], result["duplicates"], result["not_found"], result["saved"]), (3, 1, 2, 0))
        self.assert_no_collection()
        self.assertTrue(self.checker_class.call_args.kwargs["strict_timeouts"])
        for call in self.checker.find_existing_counsel_any.call_args_list:
            self.assertEqual(call.args[0], ["FDUSAP06", "FDUSAP6U"])
            self.assertIsNone(call.kwargs["expected_filename"])
        self.assertEqual([r["Status"] for r in self.artifacts.irt_check_records], ["Found", "Not found", "Not found"])
        with ZipFile(self.artifacts.summary_path) as report:
            book = ET.fromstring(report.read("xl/workbook.xml"))
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            self.assertEqual(book.find("s:sheets/s:sheet", ns).attrib["name"], "IRT Check")
            sheet = ET.fromstring(report.read("xl/worksheets/sheet1.xml"))
            self.assertEqual(len(sheet.findall("s:sheetData/s:row", ns)), 4)
            values = [node.text for node in sheet.findall(".//s:t", ns)]
            self.assertIn("EXISTING-LNI", values)
            self.assertIn("26-1001", values)
            self.assertIn("Not found", values)

    def test_all_not_found_is_successful_and_still_never_collects(self):
        result = self.run_check([None, None, None])
        self.assertTrue(result["success"])
        self.assertEqual(result["not_found"], 3)
        self.assert_no_collection()

    def test_all_found_still_never_collects(self):
        result = self.run_check([MATCH, MATCH, MATCH])
        self.assertTrue(result["success"])
        self.assertEqual(result["duplicates"], 3)
        self.assert_no_collection()

    def test_failure_keeps_partial_results_and_does_not_report_absence(self):
        result = self.run_check([MATCH, IRTDuplicateCheckError("Search timed out")])
        self.assertFalse(result["success"])
        self.assertEqual((result["checked"], result["duplicates"], result["not_found"]), (1, 1, 0))
        self.assertEqual([r["Status"] for r in self.artifacts.irt_check_records], ["Found", "Failed", "Not checked"])
        self.assert_no_collection()

    def test_cancellation_keeps_partial_results_and_closes_irt(self):
        result = self.run_check([MATCH, RunCancelled("Cancelled")])
        self.assertTrue(result["cancelled"])
        self.assertEqual([r["Status"] for r in self.artifacts.irt_check_records], ["Found", "Cancelled", "Not checked"])
        self.assert_no_collection()

    def test_cancel_before_first_check_records_unattempted_dockets(self):
        event = Event()
        event.set()
        result = self.run_check([], stop_event=event)
        self.assertTrue(result["cancelled"])
        self.assertEqual([r["Status"] for r in self.artifacts.irt_check_records], ["Not checked"] * 3)
        self.checker_class.assert_not_called()
        self.courtlink.assert_not_called()

    def test_directory_run_copies_report_only(self):
        target = Path(self.temp_path) / "source"
        result = self.run_check([MATCH, None, None], directory_copy_target=target)
        self.assertTrue(result["success"])
        self.assertEqual([p.name for p in target.iterdir()], ["Report.xlsx"])
        self.assert_no_collection()

    def test_default_mode_still_proceeds_to_courtlink_for_missing_counsel(self):
        self.checker.find_existing_counsel_any.return_value = None
        self.login.return_value = False  # Stop before real CourtLink navigation.
        result = run_searches({"6th Circuit Court": "26-1000"}, "id", "password", progress_callback=lambda *_: None)
        self.assertEqual(result["run_mode"], RUN_MODE_COLLECTION)
        self.courtlink.assert_called_once()
        self.login.assert_called_once()
        self.courtlink.return_value.quit.assert_called_once()
        self.assertFalse(self.checker_class.call_args.kwargs["strict_timeouts"])

    def test_invalid_mode_cannot_start_any_browser(self):
        result = run_searches({"6th Circuit Court": "26-1000"}, "", "", run_mode="invalid")
        self.assertFalse(result["success"])
        self.checker_class.assert_not_called()
        self.courtlink.assert_not_called()


class InputAndSearchTests(unittest.TestCase):
    def test_folder_check_includes_dockets_with_local_counsel(self):
        with test_directory() as tmp:
            root = Path(tmp) / "CA6U"
            root.mkdir()
            (root / "smd_26-1000_CA6.pdf").touch()
            (root / "smd_26-1000_CA6_Counsel.csv").touch()
            self.assertEqual(parse_pdf_directory(root)["queued_count"], 0)
            result = parse_pdf_directory(root, include_existing_counsel=True)
            self.assertEqual(result["queued_count"], 1)
            self.assertEqual(result["jobs"][0]["output_folder"], "USAP6U")

    def test_irt_query_uses_court_docket_and_counsel_filter(self):
        checker = IRTDuplicateChecker(strict_timeouts=True)
        with patch.object(checker, "_initialize"), patch.object(checker, "_set_field") as fields, \
             patch.object(checker, "_click_search_and_wait"), patch.object(checker, "_extract_records", return_value=[MATCH]), \
             patch.object(checker, "_matching_records", return_value=[MATCH]), \
             patch.object(checker, "_latest_record", return_value=MATCH):
            self.assertEqual(checker.find_existing_counsel("FDUSAP06", "26-1000", "11-05-2024", "11-05-2026"), MATCH)
        values = dict(call.args for call in fields.call_args_list)
        self.assertEqual(values[checker.COURT_CODE_XPATH], "FDUSAP06")
        self.assertEqual(values[checker.DOCKET_NUMBER_XPATH], "26-1000")
        self.assertEqual(values[checker.FILE_NAME_XPATH], "*counsel*")

    def test_timeout_is_a_failure_in_check_only_mode(self):
        checker = IRTDuplicateChecker(strict_timeouts=True)
        with patch.object(checker, "_initialize"), patch.object(checker, "_set_field"), \
             patch.object(checker, "_click_search_and_wait", side_effect=TimeoutException()):
            with self.assertRaisesRegex(IRTDuplicateCheckError, "could not be confirmed"):
                checker.find_existing_counsel("FDUSAP06", "26-1000", "11-05-2024", "11-05-2026")


class UIModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gui.main_window import CounselCollectorWindow
        with patch("gui.main_window.load_saved_user_id", return_value=""):
            self.window = CounselCollectorWindow()

    def tearDown(self):
        from PyQt5.QtCore import QEvent, QTimer
        self.app.removeEventFilter(self.window)
        for timer in self.window.findChildren(QTimer):
            timer.stop()
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_mode_changes_validation_worker_and_completion(self):
        from PyQt5.QtCore import QPoint, Qt
        from PyQt5.QtWidgets import QFrame
        w = self.window
        from PyQt5.QtWidgets import QListView
        self.assertIsInstance(w.mode_combo.view(), QListView)
        self.assertEqual(w.mode_combo.maxVisibleItems(), 2)
        self.assertEqual(w.mode_combo.objectName(), "ModeCombo")
        w.show()
        self.app.processEvents()
        expected_popup_y = w.mode_combo.mapToGlobal(QPoint(0, w.mode_combo.height())).y()
        w.mode_combo.showPopup()
        self.app.processEvents()
        popup = w.mode_combo.view().window()
        self.assertEqual(popup.geometry().top(), expected_popup_y)
        self.assertIsInstance(popup, QFrame)
        self.assertTrue(popup.windowFlags() & Qt.FramelessWindowHint)
        self.assertEqual(popup.frameShape(), QFrame.NoFrame)
        self.assertTrue(popup.testAttribute(Qt.WA_TranslucentBackground))
        w.mode_combo.hidePopup()

        w.court_combo.set_checked_items(["6th Circuit Court"], emit=True)
        w.docket_input.setText("26-1000")
        self.assertFalse(w.run_btn.isEnabled())
        w.mode_combo.setCurrentIndex(w.mode_combo.findData(RUN_MODE_IRT_CHECK))
        self.assertEqual(w.run_btn.text(), "Check IRT")
        self.assertTrue(w.run_btn.isEnabled())
        self.assertFalse(w.password_input.isEnabled())
        with patch("gui.main_window.SearchWorker") as worker, patch("gui.main_window.QThread"):
            w._start_collection()
            self.assertEqual(worker.call_args.kwargs["run_mode"], RUN_MODE_IRT_CHECK)
        w.worker = w.worker_thread = None
        w._set_final_status("Done", "success", {"run_mode": RUN_MODE_IRT_CHECK, "checked": 1, "total_dockets": 1})
        self.assertEqual(w.status_label.text(), "IRT check complete.")
        w.mode_combo.setCurrentIndex(w.mode_combo.findData(RUN_MODE_COLLECTION))
        self.assertTrue(w.password_input.isEnabled())
        self.assertFalse(w.run_btn.isEnabled())

    def test_switching_mode_reloads_loaded_folder(self):
        w = self.window
        with test_directory() as tmp:
            root = Path(tmp) / "CA6"
            root.mkdir()
            for name in ["smd_26-1000_CA6.pdf", "smd_26-1001_CA6.pdf", "smd_26-1001_CA6_Counsel.csv"]:
                (root / name).touch()
            w._load_docket_directory(str(root))
            self.assertEqual(w.dockets_by_court["6th Circuit Court"], "26-1000")
            w.mode_combo.setCurrentIndex(w.mode_combo.findData(RUN_MODE_IRT_CHECK))
            self.assertEqual(w.dockets_by_court["6th Circuit Court"], "26-1000, 26-1001")
            w.mode_combo.setCurrentIndex(w.mode_combo.findData(RUN_MODE_COLLECTION))
            self.assertEqual(w.dockets_by_court["6th Circuit Court"], "26-1000")

    def test_switch_to_collection_clears_queue_if_all_counsel_exists_locally(self):
        w = self.window
        with test_directory() as tmp:
            root = Path(tmp) / "CA6"
            root.mkdir()
            (root / "smd_26-1000_CA6.pdf").touch()
            (root / "smd_26-1000_CA6_Counsel.csv").touch()
            w.mode_combo.setCurrentIndex(w.mode_combo.findData(RUN_MODE_IRT_CHECK))
            w._load_docket_directory(str(root))
            self.assertTrue(w.run_btn.isEnabled())
            with patch.object(w, "_show_prompt"):
                w.mode_combo.setCurrentIndex(w.mode_combo.findData(RUN_MODE_COLLECTION))
            self.assertEqual(w.output_jobs, [])
            self.assertEqual(w.dockets_by_court, {})
            self.assertFalse(w.run_btn.isEnabled())
            w.mode_combo.setCurrentIndex(w.mode_combo.findData(RUN_MODE_IRT_CHECK))
            self.assertEqual(w.dockets_by_court["6th Circuit Court"], "26-1000")
            self.assertTrue(w.run_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
