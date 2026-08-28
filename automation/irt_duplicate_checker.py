from datetime import datetime
import io
import os
import re
import time

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from automation.browser_session import session
from automation.cancellation import raise_if_cancelled
from utils.docket_input import (
    canonical_usap_code,
    default_output_folder_for_court,
    unpublished_output_folder_for_court,
    usap_code_to_court_label,
)


IRT_SERVER_DOWN_MESSAGE = (
    "There is some error in the application : Some error occurred in the application.\n"
    "Please raise a webstar to contact Engineering/Admin team for resolution"
)


class IRTDuplicateCheckError(RuntimeError):
    pass


class IRTServerUnavailableError(IRTDuplicateCheckError):
    pass


IRT_COURT_CODES = {
    "USAP01": "FDUSAP01",
    "USAP1U": "FDUSAP1U",
    "USAP02": "FDUSAP02",
    "USAP2U": "FDUSAP2U",
    "USAP03": "FDUSAP03",
    "USAP3U": "FDUSAP3U",
    "USAP04": "FDUSAP04",
    "USAP4U": "FDUSAP4U",
    "USAP05": "FDUSAP05",
    "USAP5U": "FDUSAP5U",
    "USAP06": "FDUSAP06",
    "USAP6U": "FDUSAP6U",
    "USAP07": "FDUSAP07",
    "USAP7U": "FDUSAP7U",
    "USAP08": "FDUSAP08",
    "USAP8U": "FDUSAP8U",
    "USAP09": "FDUSAP09",
    "USAP9U": "FDUSAP9U",
    "USAP10": "FDUSAP10",
    "USAP10U": "FDUSAP10U",
    "USAP11": "FDUSAP11",
    "USAP11U": "FDUSAP11U",
    "USAPDC": "FDUSAPDC",
    "USADCU": "FDUSADCU",
}


def _append_unique(items, value):
    if value and value not in items:
        items.append(value)


def _filename_basename(value):
    return str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()


def _filename_match_key(value):
    basename = _filename_basename(value).casefold()
    basename = re.sub(r"\.html?$", "", basename)
    return basename.replace("_e2e", "")


def _filename_family_key(value):
    return re.sub(r"(counsel)(?:-\d+)?$", r"\1", _filename_match_key(value))


def _filename_query_variants(expected_filename):
    basename = _filename_basename(expected_filename)
    if not basename:
        return []

    if "." in basename:
        stem, _extension = basename.rsplit(".", 1)
        no_e2e_stem = re.sub(r"_e2e", "", stem, flags=re.IGNORECASE)
    else:
        no_e2e_stem = re.sub(r"_e2e", "", basename, flags=re.IGNORECASE)
    no_e2e_stem = re.sub(r"(counsel)(?:-\d+)?$", r"\1", no_e2e_stem, flags=re.IGNORECASE)
    return [f"*{no_e2e_stem}*"]


def _docket_pattern(docket_number):
    docket = str(docket_number or "").strip()
    if "-" in docket:
        left, right = docket.split("-", 1)
        return re.compile(
            rf"(?<!\d){re.escape(left)}\s*[-–—]?\s*{re.escape(right)}(?!\d)",
            re.IGNORECASE,
        )
    return re.compile(re.escape(docket), re.IGNORECASE)


def usap_codes_for_duplicate_route(route_value):
    route_value = str(route_value or "").strip()
    code = canonical_usap_code(route_value)
    court_label = usap_code_to_court_label(code) if code else route_value

    codes = []
    if court_label:
        _append_unique(codes, canonical_usap_code(default_output_folder_for_court(court_label)))
        _append_unique(codes, canonical_usap_code(unpublished_output_folder_for_court(court_label)))

    if not codes:
        _append_unique(codes, code)

    return codes


def irt_court_codes_for_route(route_value):
    court_codes = []
    for code in usap_codes_for_duplicate_route(route_value):
        court_code = IRT_COURT_CODES.get(code, "")
        _append_unique(court_codes, court_code)
    return court_codes


def irt_court_code_for_route(route_value):
    court_codes = irt_court_codes_for_route(route_value)
    return court_codes[0] if court_codes else ""


def duplicate_date_range(today=None):
    today = today or datetime.now()
    try:
        start = today.replace(year=today.year - 2)
    except ValueError:
        start = today.replace(year=today.year - 2, day=28)
    return start.strftime("%m-%d-%Y"), today.strftime("%m-%d-%Y")


def _log(logger, message, level="INFO"):
    if logger and hasattr(logger, "log"):
        logger.log(message, level=level)
    else:
        print(message)


class IRTDuplicateChecker:
    IRT_HOME_URL = "https://tcfabprod.lexisnexis.com/shared/InventoryInvoicing/"

    SEARCH_INVENTORY_XPATH = '//*[@id="menu"]/table/thead/tr/td[3]/h3/a'
    COURT_CODE_XPATH = '//*[@id="courtSearch"]'
    DOCKET_NUMBER_XPATH = '//*[@id="docketNumberSearch"]'
    FILE_NAME_XPATH = '//*[@id="originalFileNameSearch"]'
    DATE_FROM_XPATH = '//*[@id="receivedDateandTimeSearchFrom"]'
    DATE_TO_XPATH = '//*[@id="receivedDateandTimeSearchTo"]'
    SEARCH_BUTTON_XPATH = '//*[@id="search"]'
    SEARCH_TABLE_XPATH = '//*[@id="searchTable"]'
    SEARCH_TABLE_BODY_XPATH = '//*[@id="searchTable"]/tbody'
    BLOCK_OVERLAY_SELECTOR = ".blockUI.blockOverlay"

    IRT_RESULTS_TIMEOUT = 120
    IRT_STARTUP_ATTEMPTS = 3
    OCR_SCREENSHOT_PASSES = 4
    NO_RESULT_MARKERS = (
        "no matching records",
        "no data available",
        "no data found",
        "no records found",
    )

    def __init__(self, logger=None, headless=True, stop_event=None):
        self.logger = logger
        self.headless = headless
        self.stop_event = stop_event
        self.driver = None
        self._initialized = False
        self._ocr_unavailable_reason = None
        self._ocr_unavailable_logged = False

    def _create_driver(self):
        return session(headless=self.headless)

    @staticmethod
    def _normalize_page_text(text):
        return " ".join((text or "").split()).lower()

    def _is_irt_server_down_page(self):
        if not self.driver:
            return False
        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text
        except WebDriverException:
            body_text = ""
        try:
            page_source = self.driver.page_source
        except WebDriverException:
            page_source = ""

        page_text = self._normalize_page_text(f"{body_text} {page_source}")
        server_down_text = self._normalize_page_text(IRT_SERVER_DOWN_MESSAGE)
        return server_down_text in page_text or (
            "there is some error in the application" in page_text
            and "please raise a webstar" in page_text
        )

    def _wait_until_unblocked(self, timeout=90):
        def page_unblocked(driver):
            raise_if_cancelled(self.stop_event)
            try:
                overlays = driver.find_elements(By.CSS_SELECTOR, self.BLOCK_OVERLAY_SELECTOR)
                return not any(overlay.is_displayed() for overlay in overlays)
            except WebDriverException:
                return True

        WebDriverWait(self.driver, timeout).until(page_unblocked)

    def _initialize(self):
        if self._initialized and self.driver:
            return

        if not self.driver:
            self.driver = self._create_driver()

        last_error = None
        for attempt in range(1, self.IRT_STARTUP_ATTEMPTS + 1):
            raise_if_cancelled(self.stop_event)
            try:
                _log(
                    self.logger,
                    f"IRT startup attempt {attempt}/{self.IRT_STARTUP_ATTEMPTS}: opening Home Page.",
                )
                self.driver.get(self.IRT_HOME_URL)
                time.sleep(2)

                if self._is_irt_server_down_page():
                    raise IRTServerUnavailableError(IRT_SERVER_DOWN_MESSAGE)

                search_inventory = WebDriverWait(self.driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, self.SEARCH_INVENTORY_XPATH))
                )
                search_inventory.click()
                time.sleep(2)
                self._wait_until_unblocked()

                if self._is_irt_server_down_page():
                    raise IRTServerUnavailableError(IRT_SERVER_DOWN_MESSAGE)

                WebDriverWait(self.driver, 30).until(
                    EC.presence_of_element_located((By.XPATH, self.FILE_NAME_XPATH))
                )
                self._initialized = True
                return
            except IRTServerUnavailableError as exc:
                last_error = exc
                _log(self.logger, "IRT application error page is visible.", level="WARNING")
            except Exception as exc:
                last_error = exc
                if self._is_irt_server_down_page():
                    last_error = IRTServerUnavailableError(IRT_SERVER_DOWN_MESSAGE)
                    _log(self.logger, "IRT application error page is visible.", level="WARNING")
                else:
                    _log(
                        self.logger,
                        f"IRT startup attempt {attempt} failed: {type(exc).__name__}: {exc!r}",
                        level="WARNING",
                    )

            if attempt < self.IRT_STARTUP_ATTEMPTS:
                try:
                    self.driver.refresh()
                    time.sleep(2)
                except WebDriverException:
                    pass

        self._initialized = False
        if isinstance(last_error, IRTServerUnavailableError):
            raise last_error
        raise IRTDuplicateCheckError(f"Could not initialize IRT duplicate checker: {last_error!r}")

    def _set_field(self, xpath, value):
        self._wait_until_unblocked()
        field = WebDriverWait(self.driver, 30).until(EC.presence_of_element_located((By.XPATH, xpath)))
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", field)
            field.clear()
            field.send_keys(value)
        except WebDriverException:
            self.driver.execute_script(
                """
                const field = arguments[0];
                const value = arguments[1];
                field.focus();
                field.value = "";
                field.dispatchEvent(new Event("input", { bubbles: true }));
                field.value = value;
                field.dispatchEvent(new Event("input", { bubbles: true }));
                field.dispatchEvent(new Event("change", { bubbles: true }));
                """,
                field,
                value,
            )

    def _wait_for_results_ready(self):
        def has_no_data_cell(driver):
            empty_cell_selectors = (
                "#searchTable td.dataTables_empty",
                "#searchTable tbody td[colspan='16']",
            )
            for selector in empty_cell_selectors:
                try:
                    for cell in driver.find_elements(By.CSS_SELECTOR, selector):
                        if "no data found" in self._normalize_page_text(cell.text):
                            return True
                except WebDriverException:
                    continue
            return False

        def table_ready(driver):
            try:
                table_body = driver.find_element(By.XPATH, self.SEARCH_TABLE_BODY_XPATH)
                if has_no_data_cell(driver):
                    return True

                table_text = self._normalize_page_text(table_body.text)
                if any(marker in table_text for marker in self.NO_RESULT_MARKERS):
                    return True

                rows = table_body.find_elements(By.TAG_NAME, "tr")
                if not rows:
                    return False

                for row in rows:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) >= 2 and cells[1].text.strip():
                        return True
                return False
            except WebDriverException:
                return False

        WebDriverWait(self.driver, self.IRT_RESULTS_TIMEOUT).until(table_ready)

    def _click_search_and_wait(self):
        self._wait_until_unblocked()
        search_button = WebDriverWait(self.driver, 30).until(
            EC.element_to_be_clickable((By.XPATH, self.SEARCH_BUTTON_XPATH))
        )
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", search_button)
        self._wait_until_unblocked()
        try:
            search_button.click()
        except WebDriverException:
            self._wait_until_unblocked()
            try:
                search_button = WebDriverWait(self.driver, 30).until(
                    EC.element_to_be_clickable((By.XPATH, self.SEARCH_BUTTON_XPATH))
                )
                search_button.click()
            except WebDriverException:
                search_button = WebDriverWait(self.driver, 30).until(
                    EC.presence_of_element_located((By.XPATH, self.SEARCH_BUTTON_XPATH))
                )
                self.driver.execute_script("arguments[0].click();", search_button)
        WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located((By.XPATH, self.SEARCH_TABLE_XPATH))
        )
        self._wait_for_results_ready()
        self._wait_until_unblocked()
        time.sleep(0.25)

    def _parse_received_datetime(self, received_date):
        normalized = " ".join((received_date or "").split())
        if not normalized:
            return None
        for date_format in (
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%m/%d/%Y",
            "%m/%d/%Y %I:%M %p",
            "%m/%d/%Y %I:%M:%S %p",
            "%m/%d/%Y %H:%M",
            "%m/%d/%Y %H:%M:%S",
            "%m-%d-%Y",
            "%m-%d-%Y %I:%M %p",
            "%m-%d-%Y %I:%M:%S %p",
            "%m-%d-%Y %H:%M",
            "%m-%d-%Y %H:%M:%S",
        ):
            try:
                return datetime.strptime(normalized, date_format)
            except ValueError:
                continue
        return None

    def _extract_records(self):
        table_body = self.driver.find_element(By.XPATH, self.SEARCH_TABLE_BODY_XPATH)
        try:
            extraction = self.driver.execute_script(
                """
                const tbody = arguments[0];
                const table = tbody.closest("table");
                const textOf = (cells, index) => {
                    if (index >= cells.length || !cells[index]) return "";
                    return (cells[index].textContent || "").trim();
                };
                const normalizeHeader = (value) =>
                    (value || "").replace(/\\s+/g, " ").trim().toLowerCase();
                const headerTexts = table
                    ? Array.from(table.querySelectorAll("thead tr:last-child th")).map(
                        (th) => normalizeHeader(th.textContent)
                    )
                    : [];
                const headerIndex = (headerName, fallbackIndex) => {
                    const foundIndex = headerTexts.indexOf(normalizeHeader(headerName));
                    return foundIndex >= 0 ? foundIndex : fallbackIndex;
                };
                const hrefsOf = (row) =>
                    Array.from(row.querySelectorAll("a[href]"))
                        .map((link) => (link.getAttribute("href") || "").trim())
                        .filter((href) => href && !href.toLowerCase().startsWith("javascript:"));
                const lniIndex = headerIndex("LNI", 0);
                const fileNameIndex = headerIndex("File Name", 1);
                const receivedDateIndex = headerIndex("Received Date", 6);
                const rows = Array.from(tbody.querySelectorAll("tr"));
                const records = [];
                for (const row of rows) {
                    const cells = Array.from(row.querySelectorAll("td"));
                    if (cells.length < 2) continue;
                    const fileName = textOf(cells, fileNameIndex);
                    if (!fileName) continue;
                    records.push({
                        "LNI": textOf(cells, lniIndex),
                        "File Name": fileName,
                        "Received Date": textOf(cells, receivedDateIndex),
                        "Links": hrefsOf(row),
                    });
                }
                return records;
                """,
                table_body,
            )
            return extraction or []
        except WebDriverException:
            records = []
            for row in table_body.find_elements(By.TAG_NAME, "tr"):
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) < 2:
                    continue
                file_name = cells[1].text.strip()
                if not file_name:
                    continue
                records.append(
                    {
                        "LNI": cells[0].text.strip(),
                        "File Name": file_name,
                        "Received Date": cells[6].text.strip() if len(cells) > 6 else "",
                        "Links": [
                            link.get_attribute("href") or ""
                            for link in row.find_elements(By.CSS_SELECTOR, "a[href]")
                        ],
                    }
                )
            return records

    def _latest_record(self, records):
        latest = None
        latest_dt = None
        for record in records:
            received_dt = self._parse_received_datetime(record.get("Received Date", ""))
            if latest is None or (received_dt is not None and (latest_dt is None or received_dt > latest_dt)):
                latest = record
                latest_dt = received_dt
        return latest

    def _matching_records(self, records, docket_number, expected_filename=None):
        if expected_filename:
            expected_key = _filename_match_key(expected_filename)
            return [
                record
                for record in records
                if _filename_match_key(record.get("File Name", "")) == expected_key
            ]

        docket_pattern = re.compile(re.escape(docket_number), re.IGNORECASE)
        matching_records = [
            record
            for record in records
            if docket_pattern.search(record.get("File Name", ""))
        ]
        return matching_records or records

    def _filename_family_records(self, records, expected_filename, include_exact=False):
        family_key = _filename_family_key(expected_filename)
        exact_key = _filename_match_key(expected_filename)
        return [
            record
            for record in records
            if _filename_family_key(record.get("File Name", "")) == family_key
            and (include_exact or _filename_match_key(record.get("File Name", "")) != exact_key)
        ]

    def _record_links(self, record):
        links = []
        for link in record.get("Links") or []:
            link = str(link or "").strip()
            if link and link not in links:
                links.append(link)
        return links[:3]

    def _ocr_modules(self):
        if self._ocr_unavailable_reason:
            return None, None

        try:
            from PIL import Image, ImageFilter, ImageOps
            import pytesseract
        except ImportError as exc:
            self._ocr_unavailable_reason = (
                "OCR fallback unavailable because Pillow/pytesseract is not installed."
            )
            return None, None

        tesseract_cmd = os.getenv("TESSERACT_CMD", "").strip()
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

        try:
            pytesseract.get_tesseract_version()
        except Exception as exc:
            self._ocr_unavailable_reason = (
                "OCR fallback unavailable because the Tesseract engine could not be started. "
                "Install Tesseract OCR or set TESSERACT_CMD to tesseract.exe."
            )
            return None, None

        return (Image, ImageFilter, ImageOps), pytesseract

    def _log_ocr_unavailable_once(self):
        if self._ocr_unavailable_reason and not self._ocr_unavailable_logged:
            _log(self.logger, self._ocr_unavailable_reason, level="WARNING")
            self._ocr_unavailable_logged = True

    def _ocr_page_mentions_docket(self, docket_number):
        image_modules, pytesseract = self._ocr_modules()
        if not image_modules or not pytesseract:
            self._log_ocr_unavailable_once()
            return False

        Image, ImageFilter, ImageOps = image_modules
        docket_pattern = _docket_pattern(docket_number)
        try:
            viewport_height = int(
                self.driver.execute_script("return Math.max(window.innerHeight || 0, 700);")
                or 700
            )
            scroll_height = int(
                self.driver.execute_script(
                    "return Math.max(document.body.scrollHeight || 0, document.documentElement.scrollHeight || 0);"
                )
                or viewport_height
            )
        except WebDriverException:
            viewport_height = 700
            scroll_height = viewport_height

        max_scroll = max(0, scroll_height - viewport_height)
        if self.OCR_SCREENSHOT_PASSES <= 1 or max_scroll == 0:
            offsets = [0]
        else:
            offsets = [
                int(max_scroll * index / (self.OCR_SCREENSHOT_PASSES - 1))
                for index in range(self.OCR_SCREENSHOT_PASSES)
            ]

        for offset in offsets:
            raise_if_cancelled(self.stop_event)
            try:
                self.driver.execute_script("window.scrollTo(0, arguments[0]);", offset)
                time.sleep(0.25)
                png_bytes = self.driver.get_screenshot_as_png()
                image = Image.open(io.BytesIO(png_bytes))
                image = ImageOps.grayscale(image)
                image = image.resize((image.width * 2, image.height * 2))
                image = ImageOps.autocontrast(image)
                image = image.filter(ImageFilter.SHARPEN)
                text = pytesseract.image_to_string(image, config="--psm 6")
                if docket_pattern.search(text):
                    return True
            except Exception as exc:
                self._ocr_unavailable_reason = f"OCR fallback failed while reading the IRT candidate page: {exc}"
                self._log_ocr_unavailable_once()
                return False

        return False

    def _linked_record_mentions_docket(self, record, docket_number):
        links = self._record_links(record)
        if not links:
            return False

        original_handle = self.driver.current_window_handle
        docket_pattern = _docket_pattern(docket_number)
        for link in links:
            raise_if_cancelled(self.stop_event)
            opened_handle = None
            try:
                existing_handles = set(self.driver.window_handles)
                self.driver.execute_script("window.open(arguments[0], '_blank');", link)
                WebDriverWait(self.driver, 10).until(
                    lambda driver: len(set(driver.window_handles) - existing_handles) > 0
                )
                opened_handle = next(
                    handle for handle in self.driver.window_handles if handle not in existing_handles
                )
                self.driver.switch_to.window(opened_handle)
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                time.sleep(0.5)
                body_text = self.driver.find_element(By.TAG_NAME, "body").text
                page_source = self.driver.page_source
                if docket_pattern.search(f"{body_text}\n{page_source}"):
                    return True
                if self._ocr_page_mentions_docket(docket_number):
                    return True
            except WebDriverException:
                continue
            finally:
                try:
                    if opened_handle and opened_handle in self.driver.window_handles:
                        self.driver.close()
                except WebDriverException:
                    pass
                try:
                    if original_handle in self.driver.window_handles:
                        self.driver.switch_to.window(original_handle)
                except WebDriverException:
                    pass
        return False

    def _content_verified_records(self, records, docket_number):
        verified = []
        for record in records:
            if self._linked_record_mentions_docket(record, docket_number):
                verified.append(record)
        return verified

    def find_existing_counsel(self, court_code, docket_number, start_date, end_date, expected_filename=None):
        self._initialize()
        raise_if_cancelled(self.stop_event)

        search_attempts = []
        if expected_filename:
            for filename_query in _filename_query_variants(expected_filename):
                search_attempts.append(("", filename_query, expected_filename))
            search_attempts.append((docket_number, "*counsel*", expected_filename))
        else:
            search_attempts.append((docket_number, "*counsel*", None))

        for docket_query, filename_query, filename_filter in search_attempts:
            _log(
                self.logger,
                f"Checking IRT duplicate: court={court_code}, docket={docket_number}, filename={filename_query}",
            )

            self._set_field(self.COURT_CODE_XPATH, court_code)
            self._set_field(self.DOCKET_NUMBER_XPATH, docket_query)
            self._set_field(self.FILE_NAME_XPATH, filename_query)
            self._set_field(self.DATE_FROM_XPATH, start_date)
            self._set_field(self.DATE_TO_XPATH, end_date)

            try:
                self._click_search_and_wait()
            except TimeoutException:
                _log(self.logger, f"No IRT results confirmed for docket {docket_number}.")
                continue

            extracted_records = self._extract_records()
            records = self._matching_records(extracted_records, docket_number, expected_filename=filename_filter)
            family_records = (
                self._filename_family_records(extracted_records, filename_filter, include_exact=True)
                if filename_filter
                else []
            )
            content_verification_required = filename_filter and len(family_records) > 1
            if content_verification_required:
                records = self._content_verified_records(family_records, docket_number)
                if not records:
                    _log(
                        self.logger,
                        f"Found {len(family_records)} related IRT counsel filename(s) for {docket_number}, "
                        "but none could be content-verified; not recycling an LNI.",
                        level="WARNING",
                    )
            latest = self._latest_record(records)
            if latest:
                verification_note = " content-verified" if content_verification_required else ""
                _log(
                    self.logger,
                    f"IRT duplicate{verification_note} found for {docket_number}: "
                    f"LNI={latest.get('LNI', '')}, "
                    f"Received Date={latest.get('Received Date', '')}",
                )
                return latest

            if filename_filter and not content_verification_required:
                candidates = self._filename_family_records(extracted_records, filename_filter)
                verified_records = self._content_verified_records(candidates, docket_number)
                latest = self._latest_record(verified_records)
                if latest:
                    _log(
                        self.logger,
                        f"IRT duplicate content-verified for {docket_number}: "
                        f"LNI={latest.get('LNI', '')}, Received Date={latest.get('Received Date', '')}",
                    )
                    return latest
                if candidates:
                    _log(
                        self.logger,
                        f"Found {len(candidates)} related IRT counsel filename(s) for {docket_number}, "
                        "but none could be content-verified; not recycling an LNI.",
                        level="WARNING",
                    )

        _log(self.logger, f"No IRT duplicate found for {docket_number}.")
        return None

    def find_existing_counsel_any(self, court_codes, docket_number, start_date, end_date, expected_filename=None):
        matches = []
        for court_code in court_codes:
            raise_if_cancelled(self.stop_event)
            record = self.find_existing_counsel(
                court_code,
                docket_number,
                start_date,
                end_date,
                expected_filename=expected_filename,
            )
            if not record:
                continue
            match = dict(record)
            match["IRT Court Code"] = court_code
            matches.append(match)

        if not matches:
            return None

        latest = None
        latest_dt = None
        for match in matches:
            received_dt = self._parse_received_datetime(match.get("Received Date", ""))
            if latest is None or (received_dt is not None and (latest_dt is None or received_dt > latest_dt)):
                latest = match
                latest_dt = received_dt

        if len(matches) > 1:
            _log(
                self.logger,
                f"Using most recent IRT duplicate for {docket_number}: "
                f"court={latest.get('IRT Court Code', '')}, LNI={latest.get('LNI', '')}, "
                f"Received Date={latest.get('Received Date', '')}",
            )
        return latest

    def quit(self):
        if self.driver:
            try:
                self.driver.quit()
            except WebDriverException:
                pass
            finally:
                self.driver = None
                self._initialized = False
