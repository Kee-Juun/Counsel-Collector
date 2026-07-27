from datetime import datetime
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
                    }
                )
            return records

    def find_existing_counsel(self, court_code, docket_number, start_date, end_date):
        self._initialize()
        raise_if_cancelled(self.stop_event)
        _log(
            self.logger,
            f"Checking IRT duplicate: court={court_code}, docket={docket_number}, filename=*counsel*",
        )

        self._set_field(self.COURT_CODE_XPATH, court_code)
        self._set_field(self.DOCKET_NUMBER_XPATH, docket_number)
        self._set_field(self.FILE_NAME_XPATH, "*counsel*")
        self._set_field(self.DATE_FROM_XPATH, start_date)
        self._set_field(self.DATE_TO_XPATH, end_date)

        try:
            self._click_search_and_wait()
        except TimeoutException:
            _log(self.logger, f"No IRT results confirmed for docket {docket_number}.")
            return None

        records = [
            record
            for record in self._extract_records()
            if re.search(re.escape(docket_number), record.get("File Name", ""), re.IGNORECASE)
            or re.search(re.escape(docket_number), record.get("LNI", ""), re.IGNORECASE)
        ]
        if not records:
            records = self._extract_records()

        latest = None
        latest_dt = None
        for record in records:
            received_dt = self._parse_received_datetime(record.get("Received Date", ""))
            if latest is None or (received_dt is not None and (latest_dt is None or received_dt > latest_dt)):
                latest = record
                latest_dt = received_dt

        if latest:
            _log(
                self.logger,
                f"IRT duplicate found for {docket_number}: LNI={latest.get('LNI', '')}, "
                f"Received Date={latest.get('Received Date', '')}",
            )
        else:
            _log(self.logger, f"No IRT duplicate found for {docket_number}.")
        return latest

    def find_existing_counsel_any(self, court_codes, docket_number, start_date, end_date):
        matches = []
        for court_code in court_codes:
            raise_if_cancelled(self.stop_event)
            record = self.find_existing_counsel(court_code, docket_number, start_date, end_date)
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
