from datetime import datetime
from pathlib import Path
import re
import time

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, WebDriverException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from automation.artifacts import RunArtifacts
from automation.browser_session import session
from automation.cancellation import RunCancelled, raise_if_cancelled as _raise_if_cancelled
from automation.cancellation import sleep_with_cancel as _sleep
from automation.counsel_parser import find_document_content, scrape_and_clean_lexis
from automation.irt_duplicate_checker import (
    IRTDuplicateCheckError,
    IRTDuplicateChecker,
    duplicate_date_range,
    irt_court_codes_for_route,
)
from automation.login import LPLogin
from config.app_settings import (
    CIRCUIT_COURTS,
    DEBUG_DIR,
    LEXIS_SEARCH_URL,
    NO_RESULT_PHRASES,
    RESULT_LINK_SELECTORS,
)
from utils.docket_input import default_output_folder_for_court


def _emit(logger, message):
    if logger:
        logger.log(message)
    else:
        print(message)


def _exception_summary(exc):
    text = str(exc).strip().splitlines()
    detail = text[0].strip() if text else ""
    return f"{exc.__class__.__name__}: {detail}" if detail else exc.__class__.__name__


def _safe_filename(value):
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value.strip("_") or "snapshot"


def _output_file_label(artifacts, output_path):
    if not output_path:
        return ""
    path = Path(output_path)
    if artifacts:
        try:
            return str(path.relative_to(artifacts.run_dir))
        except ValueError:
            pass
    return str(path)


DOCKET_NUMBER_RE = re.compile(r"(?<!\d)\d{2,4}-\d{1,6}(?!\d)")
CURRENT_DOCUMENT_SELECTOR = "__current_courtlink_document__"
AUTO_UPDATE_DOCKET_BATCH_SIZE = 1


class SearchDidNotLaunchError(RuntimeError):
    pass


def save_debug_snapshot(driver, label, debug_dir=None, logger=None):
    target_dir = debug_dir or DEBUG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{_safe_filename(label)}_{timestamp}"
    html_path = target_dir / f"{base_name}.html"
    png_path = target_dir / f"{base_name}.png"

    try:
        html_path.write_text(driver.page_source, encoding="utf-8")
        _emit(logger, f"Saved debug HTML to: {html_path}")
    except (OSError, WebDriverException) as exc:
        _emit(logger, f"Could not save debug HTML: {exc}")

    try:
        driver.save_screenshot(str(png_path))
        _emit(logger, f"Saved debug screenshot to: {png_path}")
    except WebDriverException as exc:
        _emit(logger, f"Could not save debug screenshot: {exc}")

    return html_path, png_path


def _page_has_no_results_message(driver):
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    except WebDriverException:
        return False
    return any(phrase in body_text for phrase in NO_RESULT_PHRASES)


def _page_is_direct_access_loading(driver):
    try:
        current_url = driver.current_url.lower()
        body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    except WebDriverException:
        return False

    return (
        "cldirectaccessresults" in current_url
        and "this may take a minute" in body_text
    )


def _page_has_courtlink_retrieval_error(driver):
    try:
        current_url = driver.current_url.lower()
        body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    except WebDriverException:
        return False

    return (
        "cldirectaccessresults" in current_url
        and "docket could not be retrieved from the court" in body_text
    )


def _page_is_courtlink_document_loaded(driver):
    try:
        current_url = driver.current_url.lower()
    except WebDriverException:
        return False

    if "courtlinkdocument" not in current_url:
        return False

    return find_document_content(driver) is not None


def _find_result_links(driver, preferred_selector=None):
    selectors = []
    if preferred_selector:
        selectors.append(preferred_selector)
    selectors.extend(selector for selector in RESULT_LINK_SELECTORS if selector not in selectors)

    for selector in selectors:
        links = []
        seen = set()
        for link in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if not link.is_displayed():
                    continue
                text = link.text.strip()
                href = link.get_attribute("href") or ""
                key = href or text
                if not key or key in seen:
                    continue
                seen.add(key)
                links.append(link)
            except (StaleElementReferenceException, WebDriverException):
                continue
        if links:
            return selector, links

    return None, []


def _court_picker_is_open(driver):
    for selector in (".courtsContainer", ".courtlistcontainer", "div.courts"):
        try:
            if any(element.is_displayed() for element in driver.find_elements(By.CSS_SELECTOR, selector)):
                return True
        except WebDriverException:
            continue
    return False


def _court_search_label(court_label):
    label = court_label.strip()
    if label.endswith(" Court"):
        label = label[:-6]
    return label


def _court_is_already_selected(driver, court_label):
    expected = _court_search_label(court_label)
    variants = {expected}
    if expected.startswith("DC "):
        variants.add(expected.replace("DC ", "D.C. ", 1))

    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
    except WebDriverException:
        return False

    return any(variant in body_text for variant in variants)


def _close_court_picker(driver, logger=None, stop_event=None, attempts=2, log_sticky=True):
    for _ in range(max(attempts, 0)):
        _raise_if_cancelled(stop_event)
        if not _court_picker_is_open(driver):
            return True

        try:
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        except WebDriverException:
            pass
        _sleep(0.2, stop_event)
        if not _court_picker_is_open(driver):
            return True

        try:
            dropdown = driver.find_element(By.CLASS_NAME, "dropdownContainer")
            driver.execute_script("arguments[0].click();", dropdown)
        except WebDriverException:
            pass
        _sleep(0.2, stop_event)

    if _court_picker_is_open(driver):
        if log_sticky:
            _emit(logger, "Court picker stayed open after selection; using direct docket entry.")
        return False
    return True


def wait_for_result_links(driver, progress_callback, timeout=45, artifacts=None, stop_event=None):
    progress_callback(30, "Waiting for result links...")
    end_time = time.time() + timeout
    direct_access_wait_extended = False

    while time.time() < end_time:
        _raise_if_cancelled(stop_event)
        selector, links = _find_result_links(driver)
        if links:
            _emit(artifacts, f"Found {len(links)} result link(s) with selector: {selector}")
            progress_callback(35, f"Found {len(links)} result link(s).")
            return selector, links

        if _page_is_courtlink_document_loaded(driver):
            _emit(artifacts, "CourtLink opened the docket document directly.")
            progress_callback(35, "Docket document loaded.")
            return CURRENT_DOCUMENT_SELECTOR, [None]

        if _page_has_no_results_message(driver):
            _emit(artifacts, "Lexis page appears to show no results.")
            progress_callback(35, "No results message detected.")
            save_debug_snapshot(
                driver,
                "no_results_message",
                debug_dir=artifacts.debug_dir if artifacts else None,
                logger=artifacts,
            )
            return None, []

        if _page_has_courtlink_retrieval_error(driver):
            _emit(artifacts, "CourtLink reported that the docket could not be retrieved from the court.")
            progress_callback(35, "CourtLink could not retrieve this docket.")
            save_debug_snapshot(
                driver,
                "courtlink_retrieval_error",
                debug_dir=artifacts.debug_dir if artifacts else None,
                logger=artifacts,
            )
            return None, []

        if not direct_access_wait_extended and _page_is_direct_access_loading(driver):
            direct_access_wait_extended = True
            end_time = max(end_time, time.time() + 120)
            _emit(artifacts, "CourtLink is updating docket from court; extending result wait.")
            progress_callback(32, "CourtLink is updating docket from court...")

        _sleep(1, stop_event)

    _emit(artifacts, "No result links found before timeout.")
    if _search_form_is_visible(driver):
        _emit(
            artifacts,
            "Search form is still visible after result wait; CourtLink search likely did not launch or did not transition.",
        )
    _emit(artifacts, f"Current result page: {driver.title} - {driver.current_url}")
    progress_callback(35, "No result links found. Saved debug snapshot.")
    save_debug_snapshot(
        driver,
        "no_result_links",
        debug_dir=artifacts.debug_dir if artifacts else None,
        logger=artifacts,
    )
    return None, []


def _open_result_in_new_tab(driver, link):
    original_handles = set(driver.window_handles)
    href = link.get_attribute("href") or ""

    if href and not href.lower().startswith("javascript"):
        driver.execute_script("window.open(arguments[0], '_blank');", href)
    else:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
        ActionChains(driver).key_down(Keys.CONTROL).click(link).key_up(Keys.CONTROL).perform()

    WebDriverWait(driver, 10).until(lambda d: len(set(d.window_handles) - original_handles) > 0)
    new_handle = next(handle for handle in driver.window_handles if handle not in original_handles)
    driver.switch_to.window(new_handle)


def _extract_result_urls(links):
    result_urls = []
    for link in links:
        try:
            href = link.get_attribute("href") or ""
        except (StaleElementReferenceException, WebDriverException):
            return []
        if not href or href.lower().startswith("javascript"):
            return []
        result_urls.append(href)
    return result_urls


def _extract_result_dockets(links):
    result_dockets = []
    for link in links:
        try:
            candidates = [
                link.text or "",
                link.get_attribute("aria-label") or "",
                link.get_attribute("title") or "",
            ]
        except (StaleElementReferenceException, WebDriverException):
            return []

        match = None
        for text in candidates:
            match = DOCKET_NUMBER_RE.search(text)
            if match:
                break
        result_dockets.append(match.group(0) if match else "")
    return result_dockets


def _renamed_counsel_filename(artifacts, docket="", output_folder=""):
    docket = _extract_docket_number(docket)
    if not artifacts or not docket:
        return ""

    for row in artifacts.renamed_records:
        renamed = str(row.get("RENAMED") or "").strip()
        if "counsel" not in renamed.lower():
            continue

        row_text = f"{row.get('FEEDBACK FORM FILENAME', '')} {renamed}"
        if _extract_docket_number(row_text) != docket:
            continue

        row_court = str(row.get("COURT") or "").strip()
        if output_folder and row_court and row_court not in {output_folder, "USAPDCORD"}:
            continue
        return renamed

    return ""


def select_circuit_court_by_id(driver, checkbox_id, progress_callback, logger=None, stop_event=None):
    _raise_if_cancelled(stop_event)
    wait = WebDriverWait(driver, 20)
    dropdown = wait.until(EC.element_to_be_clickable((By.CLASS_NAME, "dropdownContainer")))
    dropdown.click()
    _sleep(1, stop_event)
    _raise_if_cancelled(stop_event)
    wait.until(EC.element_to_be_clickable((By.ID, "33a3ef2a5b4cf8f1cafa5539f24b41dd"))).click()
    _sleep(1, stop_event)
    _raise_if_cancelled(stop_event)
    wait.until(EC.element_to_be_clickable((By.ID, "e2bb0d757eac5b62f8da255b70c652b3"))).click()
    _sleep(1, stop_event)
    _raise_if_cancelled(stop_event)

    for checkbox in driver.find_elements(By.CLASS_NAME, "courtCheckbox"):
        _raise_if_cancelled(stop_event)
        try:
            if checkbox.is_selected():
                checkbox.click()
        except WebDriverException:
            continue

    checkbox = wait.until(EC.presence_of_element_located((By.ID, checkbox_id)))
    if not checkbox.is_selected():
        checkbox.click()

    selected_name = next((k for k, v in CIRCUIT_COURTS.items() if v == checkbox_id), "Unknown Court")
    progress_callback(20, f"Selected Court: {selected_name}")
    _emit(logger, f"Selected Court: {selected_name}")
    _close_court_picker(driver, logger=logger, stop_event=stop_event)


def _switch_to_new_courtlink_tab(driver, known_handles=None, logger=None):
    if not known_handles:
        return False

    try:
        new_handles = [handle for handle in driver.window_handles if handle not in known_handles]
    except WebDriverException:
        return False

    if not new_handles:
        return False

    try:
        new_handle = new_handles[-1]
        driver.switch_to.window(new_handle)
        _emit(logger, f"CourtLink opened a new results tab: {driver.current_url}")
        return True
    except WebDriverException:
        return False


def _close_extra_tabs(driver, logger=None):
    try:
        handles = list(driver.window_handles)
        current = driver.current_window_handle
    except WebDriverException:
        return

    if len(handles) <= 1:
        return

    kept_handle = current if current in handles else handles[-1]
    for handle in handles:
        if handle == kept_handle:
            continue
        try:
            driver.switch_to.window(handle)
            driver.close()
        except WebDriverException:
            continue

    try:
        driver.switch_to.window(kept_handle)
        _emit(logger, "Closed extra CourtLink browser tab(s) before starting the next search.")
    except WebDriverException:
        try:
            driver.switch_to.window(driver.window_handles[-1])
        except WebDriverException:
            pass


def _activate_reusable_search_form(driver, court_label, logger=None):
    try:
        handles = list(driver.window_handles)
        original = driver.current_window_handle
    except WebDriverException:
        return False

    selected_handle = None
    for handle in reversed(handles):
        try:
            driver.switch_to.window(handle)
            if _search_form_is_visible(driver) and _court_is_already_selected(driver, court_label):
                selected_handle = handle
                break
        except WebDriverException:
            continue

    if not selected_handle:
        try:
            driver.switch_to.window(original)
        except WebDriverException:
            pass
        return False

    for handle in handles:
        if handle == selected_handle:
            continue
        try:
            driver.switch_to.window(handle)
            driver.close()
        except WebDriverException:
            continue

    try:
        driver.switch_to.window(selected_handle)
        _emit(logger, f"Reusing open CourtLink search form for {court_label}.")
        return True
    except WebDriverException:
        return False


def _search_transitioned(driver, previous_url, known_handles=None, logger=None):
    if _switch_to_new_courtlink_tab(driver, known_handles=known_handles, logger=logger):
        return True
    if driver.current_url != previous_url:
        return True
    if _find_result_links(driver)[1]:
        return True
    return _page_has_no_results_message(driver)


def _search_form_is_visible(driver):
    try:
        docket_input = driver.find_element(By.ID, "docketNumberInput")
        search_button = driver.find_element(By.ID, "triggersearch")
        return docket_input.is_displayed() and search_button.is_displayed()
    except WebDriverException:
        return False


def _wait_for_search_transition(driver, previous_url, timeout=12, stop_event=None, known_handles=None, logger=None):
    try:
        WebDriverWait(driver, timeout).until(
            lambda active_driver: (
                _raise_if_cancelled(stop_event)
                or _search_transitioned(
                    active_driver,
                    previous_url,
                    known_handles=known_handles,
                    logger=logger,
                )
            )
        )
        return True
    except TimeoutException:
        return False


def _search_button_or_transition(driver, previous_url, known_handles=None, logger=None):
    if _search_transitioned(driver, previous_url, known_handles=known_handles, logger=logger):
        return "transitioned"
    try:
        search_button = driver.find_element(By.ID, "triggersearch")
        if search_button.is_displayed() and search_button.is_enabled():
            return search_button
    except WebDriverException:
        return False
    return False


def _is_checked(element):
    try:
        if element.is_selected():
            return True
        for attribute in ("checked", "aria-checked"):
            value = str(element.get_attribute(attribute) or "").strip().lower()
            if value in {"true", "checked", "1"}:
                return True
    except WebDriverException:
        return False
    return False


def _enable_docket_auto_update(driver, logger=None, stop_event=None):
    _raise_if_cancelled(stop_event)
    used_js_fallback = False
    try:
        checkbox = WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.ID, "autoupdate")))
    except TimeoutException:
        _emit(logger, "Docket auto-update checkbox was not available; continuing.")
        return

    if _is_checked(checkbox):
        _emit(logger, "Docket auto-update is enabled.")
        return

    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", checkbox)
        WebDriverWait(driver, 8).until(EC.element_to_be_clickable((By.ID, "autoupdate"))).click()
    except WebDriverException:
        used_js_fallback = True
        driver.execute_script(
            """
            const checkbox = arguments[0];
            if (!checkbox.checked) {
                checkbox.click();
            }
            if (!checkbox.checked) {
                checkbox.checked = true;
                checkbox.dispatchEvent(new Event("input", { bubbles: true }));
                checkbox.dispatchEvent(new Event("change", { bubbles: true }));
            }
            """,
            checkbox,
        )

    if _is_checked(checkbox):
        if used_js_fallback:
            _emit(logger, "Docket auto-update enabled with JavaScript fallback.")
        else:
            _emit(logger, "Docket auto-update enabled.")
    else:
        _emit(logger, "Warning: docket auto-update could not be confirmed.")


def submit_docket_search(driver, docket_numbers, logger=None, stop_event=None):
    _raise_if_cancelled(stop_event)
    wait = WebDriverWait(driver, 15)
    _close_court_picker(driver, logger=logger, stop_event=stop_event, attempts=1, log_sticky=False)

    docket_input = wait.until(EC.presence_of_element_located((By.ID, "docketNumberInput")))
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", docket_input)
    wait.until(EC.visibility_of_element_located((By.ID, "docketNumberInput")))
    try:
        docket_input.clear()
        docket_input.send_keys(docket_numbers)
    except WebDriverException:
        _emit(logger, "Docket field interaction was blocked; setting docket numbers with JavaScript.")
        driver.execute_script(
            """
            const input = arguments[0];
            const value = arguments[1];
            input.focus();
            input.value = "";
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.value = value;
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.dispatchEvent(new Event("change", { bubbles: true }));
            """,
            docket_input,
            docket_numbers,
        )

    _enable_docket_auto_update(driver, logger=logger, stop_event=stop_event)

    _raise_if_cancelled(stop_event)
    previous_url = driver.current_url
    known_handles = set(driver.window_handles)
    try:
        docket_input.send_keys(Keys.ENTER)
        _emit(logger, "Submitted docket search with Enter.")
    except WebDriverException:
        _emit(logger, "Enter submission was blocked; trying the Search button.")

    if _wait_for_search_transition(
        driver,
        previous_url,
        timeout=8,
        stop_event=stop_event,
        known_handles=known_handles,
        logger=logger,
    ):
        return

    _emit(logger, "Enter submission did not transition; trying the Search button.")

    _raise_if_cancelled(stop_event)
    try:
        search_target = WebDriverWait(driver, 30).until(
            lambda active_driver: (
                _raise_if_cancelled(stop_event)
                or _search_button_or_transition(
                    active_driver,
                    previous_url,
                    known_handles=known_handles,
                    logger=logger,
                )
            )
        )
    except TimeoutException:
        _emit(logger, "Search button was not available; continuing to result detection.")
        return

    if search_target == "transitioned":
        _emit(logger, "Search results loaded during Search button fallback.")
        return

    search_button = search_target
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", search_button)
        search_button.click()
        _emit(logger, "Clicked CourtLink Search button.")
    except WebDriverException:
        if _search_transitioned(driver, previous_url, known_handles=known_handles, logger=logger):
            _emit(logger, "Search results loaded before the Search button click completed.")
            return
        driver.execute_script("arguments[0].click();", search_button)
        _emit(logger, "Clicked CourtLink Search button with JavaScript.")

    if _wait_for_search_transition(
        driver,
        previous_url,
        timeout=15,
        stop_event=stop_event,
        known_handles=known_handles,
        logger=logger,
    ):
        _emit(logger, "CourtLink search transitioned after Search button click.")
        return

    _emit(logger, "Search button click did not transition; trying JavaScript click.")
    _raise_if_cancelled(stop_event)
    try:
        search_button = driver.find_element(By.ID, "triggersearch")
        driver.execute_script("arguments[0].click();", search_button)
    except WebDriverException:
        if _search_transitioned(driver, previous_url, known_handles=known_handles, logger=logger):
            _emit(logger, "CourtLink search transitioned before JavaScript retry completed.")
            return
        _emit(logger, "JavaScript Search button retry was blocked; continuing to result detection.")
        return

    if _wait_for_search_transition(
        driver,
        previous_url,
        timeout=15,
        stop_event=stop_event,
        known_handles=known_handles,
        logger=logger,
    ):
        _emit(logger, "CourtLink search transitioned after JavaScript Search button retry.")
    else:
        message = "CourtLink search form did not transition after Search button retries."
        _emit(logger, message)
        raise SearchDidNotLaunchError(message)


def open_each_result(
    driver,
    progress_callback,
    output_dir,
    artifacts=None,
    court_label="",
    record_label="",
    docket_numbers="",
    output_folder_map=None,
    output_filename_map=None,
    default_output_folder="",
    stop_event=None,
):
    _raise_if_cancelled(stop_event)
    index = 0
    saved_files = []
    failed_results = 0
    base_tab = driver.current_window_handle
    selector, links = wait_for_result_links(
        driver,
        progress_callback,
        artifacts=artifacts,
        stop_event=stop_event,
    )
    refresh_timeouts = (90, 135, 180)
    refresh_attempt = 0
    while (
        not links
        and selector != CURRENT_DOCUMENT_SELECTOR
        and _page_is_direct_access_loading(driver)
        and refresh_attempt < len(refresh_timeouts)
    ):
        refresh_attempt += 1
        retry_timeout = refresh_timeouts[refresh_attempt - 1]
        _emit(
            artifacts,
            f"CourtLink direct-access update timed out; refreshing "
            f"{refresh_attempt}/{len(refresh_timeouts)} and waiting up to {retry_timeout}s.",
        )
        progress_callback(
            32,
            f"CourtLink is still updating; refresh {refresh_attempt}/{len(refresh_timeouts)}...",
        )
        driver.refresh()
        selector, links = wait_for_result_links(
            driver,
            progress_callback,
            timeout=retry_timeout,
            artifacts=artifacts,
            stop_event=stop_event,
        )
    current_document_mode = selector == CURRENT_DOCUMENT_SELECTOR
    total_links = 1 if current_document_mode else len(links)
    progress_start = 35
    progress_end = 90
    result_urls = [] if current_document_mode else _extract_result_urls(links)
    result_dockets = (
        _split_dockets(docket_numbers)[:1]
        if current_document_mode
        else _extract_result_dockets(links)
    )
    direct_navigation = not current_document_mode and len(result_urls) == total_links and total_links > 0

    if direct_navigation:
        _emit(artifacts, f"Collected {len(result_urls)} result URL(s) for direct navigation.")
    if current_document_mode:
        _emit(artifacts, "Processing current CourtLink document without opening a result link.")

    output_folder_map = output_folder_map or {}
    output_filename_map = output_filename_map or {}
    default_output_folder = default_output_folder or record_label or court_label

    def output_folder_for_result(result_docket="", case_number=""):
        for value in (result_docket, case_number):
            docket = _extract_docket_number(value)
            if docket and docket in output_folder_map:
                return output_folder_map[docket]
        return default_output_folder

    def output_filename_for_result(result_docket="", case_number=""):
        for value in (result_docket, case_number):
            docket = _extract_docket_number(value)
            if docket and docket in output_filename_map:
                return output_filename_map[docket]
        for value in (result_docket, case_number):
            docket = _extract_docket_number(value)
            filename = _renamed_counsel_filename(
                artifacts,
                docket,
                output_folder_for_result(result_docket, case_number),
            )
            if filename:
                return filename
        return ""

    def output_dir_for_result(result_docket):
        route = {"folder": output_folder_for_result(result_docket)}

        def resolver(case_number):
            route["folder"] = output_folder_for_result(result_docket, case_number)
            if artifacts:
                return artifacts.court_path(route["folder"])
            return output_dir

        return route, resolver

    if total_links == 0:
        loading_timeout = _page_is_direct_access_loading(driver)
        retrieval_error = _page_has_courtlink_retrieval_error(driver)
        no_result_message = (
            "CourtLink could not retrieve the docket from the court."
            if retrieval_error
            else (
                "CourtLink direct-access update did not finish before timeout."
                if loading_timeout
                else "No result links found."
            )
        )
        _emit(artifacts, "Done. Found 0 result links and saved 0 counsel.")
        progress_callback(100, no_result_message)
        return {
            "found": 0,
            "saved": 0,
            "failed": 0,
            "no_results": 1,
            "files": [],
            "loading_timeout": loading_timeout,
            "retrieval_error": retrieval_error,
            "record_label": default_output_folder or record_label or court_label,
            "message": no_result_message,
        }

    while index < total_links:
        _raise_if_cancelled(stop_event)
        result_url = ""
        try:
            if current_document_mode:
                result_url = driver.current_url
            elif direct_navigation:
                result_url = result_urls[index]
                driver.get(result_url)
                result_url = driver.current_url or result_url
            else:
                _, links = _find_result_links(driver, selector)
                if index >= len(links):
                    break

                _open_result_in_new_tab(driver, links[index])
                result_url = driver.current_url
            _emit(artifacts, f"Opened result {index + 1}: {result_url}")

            progress = progress_start + (index / total_links) * (progress_end - progress_start)
            result_docket = result_dockets[index] if index < len(result_dockets) else ""
            if result_docket:
                progress_callback(
                    int(progress),
                    f"Opening docket {result_docket} (result {index + 1} of {total_links})...",
                )
            else:
                progress_callback(int(progress), f"Opening result {index + 1} of {total_links}...")

            route, output_dir_resolver = output_dir_for_result(result_docket)
            scrape_result = scrape_and_clean_lexis(
                driver,
                output_dir=output_dir,
                logger=artifacts,
                output_dir_resolver=output_dir_resolver,
                output_filename_resolver=lambda case_number, result_docket=result_docket: output_filename_for_result(
                    result_docket,
                    case_number,
                ),
            )
            if scrape_result and scrape_result.get("success"):
                output_path = scrape_result.get("path")
                case_number = str(scrape_result.get("case_number", "")).strip()
                actual_record_label = route.get("folder") or record_label or court_label
                saved_files.append(output_path)
                if case_number and case_number.lower() != "unknown":
                    progress_callback(int(progress), f"Saved docket {case_number}.")
                if artifacts:
                    artifacts.add_record(
                        actual_record_label,
                        docket_numbers,
                        status="Saved",
                        case_number=case_number,
                        output_file=_output_file_label(artifacts, output_path),
                        result_url=result_url,
                        message=scrape_result.get("title", ""),
                    )
            else:
                failed_results += 1
                message = scrape_result.get("error", "Scrape failed.") if scrape_result else "Scrape failed."
                actual_record_label = route.get("folder") or record_label or court_label
                save_debug_snapshot(
                    driver,
                    f"{court_label}_result_{index + 1}_scrape_failed",
                    debug_dir=artifacts.debug_dir if artifacts else None,
                    logger=artifacts,
                )
                if artifacts:
                    artifacts.add_record(
                        actual_record_label,
                        docket_numbers,
                        status="Failed",
                        result_url=result_url,
                        message=message,
                    )

            if not direct_navigation and not current_document_mode:
                driver.close()
                driver.switch_to.window(base_tab)
            index += 1

        except RunCancelled:
            raise
        except Exception as e:
            message = f"Failed to open counsel {index + 1}: {e}"
            _emit(artifacts, message)
            failed_results += 1
            result_docket = result_dockets[index] if index < len(result_dockets) else ""
            actual_record_label = output_folder_for_result(result_docket)
            save_debug_snapshot(
                driver,
                f"{court_label}_result_{index + 1}_open_failed",
                debug_dir=artifacts.debug_dir if artifacts else None,
                logger=artifacts,
            )
            if artifacts:
                artifacts.add_record(
                    actual_record_label,
                    docket_numbers,
                    status="Failed",
                    result_url=result_url,
                    message=message,
                )
            if not direct_navigation and not current_document_mode:
                try:
                    if driver.current_window_handle != base_tab:
                        driver.close()
                    driver.switch_to.window(base_tab)
                except WebDriverException:
                    pass
            index += 1

    _emit(
        artifacts,
        f"Done. Found {total_links} result(s), saved {len(saved_files)} counsel file(s), failed {failed_results}.",
    )
    progress_callback(100, f"Saved {len(saved_files)} of {total_links} result(s).")
    return {
        "found": total_links,
        "saved": len(saved_files),
        "failed": failed_results,
        "no_results": 0,
        "files": saved_files,
    }


def _normalize_search_jobs(court_dockets):
    if isinstance(court_dockets, dict):
        items = [
            {
                "court_label": court_label,
                "docket_numbers": docket_numbers,
            }
            for court_label, docket_numbers in court_dockets.items()
        ]
    else:
        items = court_dockets

    jobs = []
    for item in items:
        if isinstance(item, dict):
            court_label = str(item.get("court_label") or item.get("court") or "").strip()
            docket_numbers = item.get("docket_numbers") or item.get("dockets") or ""
            explicit_output_folder = str(item.get("output_folder") or "").strip()
            output_filename_map = item.get("docket_output_filenames") or {}
        else:
            values = tuple(item)
            court_label = str(values[0]).strip() if len(values) > 0 else ""
            docket_numbers = values[1] if len(values) > 1 else ""
            explicit_output_folder = str(values[2]).strip() if len(values) > 2 else ""
            output_filename_map = {}

        dockets = str(docket_numbers).strip()
        if not court_label or not dockets:
            continue

        output_folder = explicit_output_folder or default_output_folder_for_court(court_label)
        progress_label = explicit_output_folder or court_label
        jobs.append(
            {
                "court_label": court_label,
                "docket_numbers": dockets,
                "output_folder": output_folder,
                "progress_label": progress_label,
                "docket_output_filenames": output_filename_map,
            }
        )
    return jobs


def _split_dockets(docket_numbers):
    return [part.strip() for part in re.split(r"[,;\n]+", str(docket_numbers)) if part.strip()]


def _extract_docket_number(value):
    match = DOCKET_NUMBER_RE.search(str(value or ""))
    return match.group(0) if match else ""


def _merge_jobs_by_court(jobs):
    merged = {}
    order = []
    for job in jobs:
        court_label = job["court_label"]
        if court_label not in merged:
            order.append(court_label)
            merged[court_label] = {
                "court_label": court_label,
                "dockets": [],
                "seen_dockets": set(),
                "output_folder": default_output_folder_for_court(court_label),
                "output_folders": [],
                "docket_output_folders": {},
                "docket_output_filenames": {},
                "progress_label": court_label,
            }

        target = merged[court_label]
        output_folder = job["output_folder"]
        if output_folder not in target["output_folders"]:
            target["output_folders"].append(output_folder)

        for docket in _split_dockets(job["docket_numbers"]):
            docket_key = _extract_docket_number(docket) or docket
            if docket_key not in target["seen_dockets"]:
                target["seen_dockets"].add(docket_key)
                target["dockets"].append(docket_key)
            target["docket_output_folders"].setdefault(docket_key, output_folder)
            output_filename = str(job.get("docket_output_filenames", {}).get(docket_key) or "").strip()
            if output_filename:
                target["docket_output_filenames"].setdefault(docket_key, output_filename)

    normalized = []
    for court_label in order:
        job = merged[court_label]
        normalized.append(
            {
                "court_label": court_label,
                "docket_numbers": ", ".join(job["dockets"]),
                "output_folder": job["output_folder"],
                "output_folders": job["output_folders"],
                "docket_output_folders": job["docket_output_folders"],
                "docket_output_filenames": job["docket_output_filenames"],
                "progress_label": job["progress_label"],
            }
        )
    return normalized


DOCKET_BATCH_SIZE = AUTO_UPDATE_DOCKET_BATCH_SIZE


def _chunk_dockets(docket_numbers, batch_size=DOCKET_BATCH_SIZE):
    dockets = _split_dockets(docket_numbers)
    return [
        ", ".join(dockets[index:index + batch_size])
        for index in range(0, len(dockets), batch_size)
    ]


def _build_search_batches(jobs):
    batches = []
    for job in jobs:
        court_label = job["court_label"]
        docket_numbers = job["docket_numbers"]
        court_batches = _chunk_dockets(docket_numbers)
        total_batches = len(court_batches)
        for batch_index, docket_batch in enumerate(court_batches, start=1):
            batch_job = dict(job)
            batch_job["docket_numbers"] = docket_batch
            batch_dockets = _split_dockets(docket_batch)
            batch_folder_map = {
                docket: job.get("docket_output_folders", {}).get(docket)
                for docket in batch_dockets
                if job.get("docket_output_folders", {}).get(docket)
            }
            batch_filename_map = {
                docket: job.get("docket_output_filenames", {}).get(docket)
                for docket in batch_dockets
                if job.get("docket_output_filenames", {}).get(docket)
            }
            batch_job["docket_output_folders"] = batch_folder_map
            batch_job["docket_output_filenames"] = batch_filename_map
            batch_job["output_folders"] = list(dict.fromkeys(batch_folder_map.values())) or job.get("output_folders", [])
            batch_job["batch_index"] = batch_index
            batch_job["batch_total"] = total_batches
            batches.append(batch_job)
    return batches


def _is_counsel_renamed_row(row):
    text = f"{row.get('FEEDBACK FORM FILENAME', '')} {row.get('RENAMED', '')}".lower()
    return "counsel" in text


def _recycled_items_key(column):
    return f"__{column}_items"


def _format_recycled_items(items):
    if len(items) == 1:
        return items[0][1]
    return " | ".join(f"{docket}: {value}" if docket else value for docket, value in items)


def _append_recycled_value(row, column, docket, value):
    value = str(value or "").strip()
    if not value:
        return

    items_key = _recycled_items_key(column)
    items = row.setdefault(items_key, [])
    entry = (str(docket or "").strip(), value)
    if entry not in items:
        items.append(entry)
    row[column] = _format_recycled_items(items)


def _main_renamed_row_index_for_counsel(renamed_rows, counsel_index, docket):
    for index in range(counsel_index - 1, -1, -1):
        row = renamed_rows[index]
        if not _is_counsel_renamed_row(row):
            return index

    for index, row in enumerate(renamed_rows):
        row_docket = _extract_docket_number(
            f"{row.get('FEEDBACK FORM FILENAME', '')} {row.get('RENAMED', '')}"
        )
        if row_docket == docket and not _is_counsel_renamed_row(row):
            return index

    return None


def _annotate_recycled_counsel(renamed_rows, route, docket, record):
    lni = record.get("LNI", "")
    received_date = record.get("Received Date", "")
    output_folder = route.get("output_folder", "")
    filename_map = route.get("docket_output_filenames", {}) or {}
    expected_filename = filename_map.get(docket) or f"LDC_SMD_{docket}counsel.htm"

    for index, row in enumerate(renamed_rows):
        row_docket = _extract_docket_number(
            f"{row.get('FEEDBACK FORM FILENAME', '')} {row.get('RENAMED', '')}"
        )
        if row_docket == docket and _is_counsel_renamed_row(row):
            main_index = _main_renamed_row_index_for_counsel(renamed_rows, index, docket)
            if main_index is not None:
                main_row = renamed_rows[main_index]
                _append_recycled_value(main_row, "RECYCLED COUNSEL LNI", docket, lni)
                _append_recycled_value(main_row, "MOST RECENT RECEIVED DATE", docket, received_date)
                renamed_rows.pop(index)
                return

            row["RECYCLED COUNSEL LNI"] = lni
            row["MOST RECENT RECEIVED DATE"] = received_date
            return

    renamed_rows.append(
        {
            "COURT": output_folder,
            "FEEDBACK FORM FILENAME": "",
            "RENAMED": expected_filename,
            "RECYCLED COUNSEL LNI": lni,
            "MOST RECENT RECEIVED DATE": received_date,
        }
    )


def _filter_filename_map(filename_map, kept_dockets):
    kept = set(kept_dockets)
    return {
        docket: filename
        for docket, filename in (filename_map or {}).items()
        if docket in kept
    }


def _run_irt_duplicate_preflight(
    output_routes,
    renamed_rows,
    artifacts,
    headless,
    stop_event,
    progress_callback,
):
    renamed_rows = [dict(row) for row in (renamed_rows or [])]
    total_dockets = sum(len(_split_dockets(route.get("docket_numbers", ""))) for route in output_routes)
    if total_dockets == 0:
        return {
            "output_routes": output_routes,
            "renamed_rows": renamed_rows,
            "duplicates": 0,
        }

    start_date, end_date = duplicate_date_range()
    artifacts.log(f"Starting IRT duplicate preflight for {total_dockets} docket(s).")
    artifacts.log(f"IRT received date range: {start_date} to {end_date}")
    progress_callback(0, f"Starting IRT duplicate check for {total_dockets} docket(s)...")

    checker = IRTDuplicateChecker(logger=artifacts, headless=headless, stop_event=stop_event)
    filtered_routes = []
    duplicate_count = 0
    processed = 0

    try:
        for route in output_routes:
            output_folder = route.get("output_folder", "")
            court_codes = irt_court_codes_for_route(output_folder)
            if not court_codes:
                raise IRTDuplicateCheckError(f"No IRT court code mapping for output route: {output_folder}")
            artifacts.log(
                f"IRT duplicate court pass(es) for {output_folder}: {', '.join(court_codes)}"
            )

            kept_dockets = []
            for docket in _split_dockets(route.get("docket_numbers", "")):
                _raise_if_cancelled(stop_event)
                processed += 1
                progress_callback(
                    max(1, min(99, int(processed / total_dockets * 100))),
                    f"Checking IRT duplicate {processed}/{total_dockets}: {docket}",
                )
                record = checker.find_existing_counsel_any(court_codes, docket, start_date, end_date)
                if record:
                    duplicate_count += 1
                    _annotate_recycled_counsel(renamed_rows, route, docket, record)
                    continue
                kept_dockets.append(docket)

            if kept_dockets:
                filtered_route = dict(route)
                filtered_route["docket_numbers"] = ", ".join(kept_dockets)
                filtered_route["docket_output_filenames"] = _filter_filename_map(
                    route.get("docket_output_filenames"),
                    kept_dockets,
                )
                filtered_routes.append(filtered_route)

        artifacts.log(
            f"IRT duplicate preflight complete: {duplicate_count} duplicate counsel docket(s), "
            f"{sum(len(_split_dockets(route.get('docket_numbers', ''))) for route in filtered_routes)} new docket(s)."
        )
        progress_callback(
            100,
            f"IRT duplicate check complete: {duplicate_count} duplicate(s), "
            f"{total_dockets - duplicate_count} new docket(s).",
        )
        return {
            "output_routes": filtered_routes,
            "renamed_rows": renamed_rows,
            "duplicates": duplicate_count,
        }
    finally:
        checker.quit()


def _empty_result(message):
    return {
        "success": False,
        "message": message,
        "found": 0,
        "saved": 0,
        "failed": 0,
        "no_results": 0,
        "cancelled": False,
        "files": [],
        "run_dir": "",
        "log_file": "",
        "summary_file": "",
        "directory_copy_target": "",
        "directory_copied_paths": [],
    }


def run_searches(
    court_dockets,
    username,
    password,
    progress_callback=None,
    headless=True,
    stop_event=None,
    renamed_rows=None,
    directory_copy_target=None,
):
    if progress_callback is None:
        def progress_callback(percent, text):
            print(f"[{percent}%] {text}")

    raw_output_routes = _normalize_search_jobs(court_dockets)
    if not raw_output_routes:
        return _empty_result("No court/docket jobs were provided.")

    output_routes = raw_output_routes
    jobs = []
    search_batches = []
    output_folder_names = []
    artifacts = RunArtifacts.create()
    driver = None
    result = {
        "success": False,
        "message": "Search did not complete.",
        "found": 0,
        "saved": 0,
        "failed": 0,
        "no_results": 0,
        "duplicates": 0,
        "cancelled": False,
        "files": [],
        "run_dir": str(artifacts.run_dir),
        "log_file": str(artifacts.log_path),
        "summary_file": str(artifacts.summary_path),
        "directory_copy_target": str(directory_copy_target or ""),
        "directory_copied_paths": [],
    }

    try:
        _raise_if_cancelled(stop_event)
        preflight = _run_irt_duplicate_preflight(
            raw_output_routes,
            renamed_rows,
            artifacts,
            headless,
            stop_event,
            progress_callback,
        )
        output_routes = preflight["output_routes"]
        artifacts.renamed_records.extend(preflight["renamed_rows"])
        result["duplicates"] = preflight["duplicates"]

        if not output_routes:
            result["success"] = True
            result["message"] = (
                f"Skipped {result['duplicates']} docket counsel(s) already found in IRT. "
                "No CourtLink collection was needed."
            )
            artifacts.log(result["message"])
            artifacts.log_run_summary(0, 0, 0, 0)
            return result

        jobs = _merge_jobs_by_court(output_routes)
        search_batches = _build_search_batches(jobs)
        output_folder_names = list(
            dict.fromkeys(route["output_folder"] for route in output_routes if route.get("output_folder"))
        )
        artifacts.log(
            f"Starting run with {len(jobs)} court search job(s), "
            f"{len(output_routes)} output route(s), and {len(search_batches)} search batch(es)."
        )
        progress_callback(0, "Starting CourtLink collection...")
        progress_callback(3, "Initializing CourtLink...")
        driver = session(headless=headless)
        _raise_if_cancelled(stop_event)
        progress_callback(5, "Logging in...")
        if not LPLogin(username, password, driver, stop_event=stop_event):
            result["message"] = "Login failed."
            result["failed"] = 1
            artifacts.log("Login failed.", level="ERROR")
            artifacts.log_run_summary(0, 0, result["failed"], 0)
            return result
        artifacts.log("Login completed.")

        total_jobs = len(search_batches)
        for job_index, job in enumerate(search_batches, start=1):
            _raise_if_cancelled(stop_event)
            court_label = job["court_label"]
            docket_numbers = job["docket_numbers"]
            output_folder = job["output_folder"]
            output_folders = job.get("output_folders") or [output_folder]
            fallback_output_folder = output_folders[0] if len(output_folders) == 1 else output_folder
            output_folder_map = job.get("docket_output_folders", {})
            output_filename_map = job.get("docket_output_filenames", {})
            progress_label = job["progress_label"]
            batch_index = job["batch_index"]
            batch_total = job["batch_total"]
            batch_label = (
                f"{progress_label} batch {batch_index} of {batch_total}"
                if batch_total > 1
                else progress_label
            )
            log_label = (
                f"{batch_label} ({court_label})"
                if progress_label != court_label
                else batch_label
            )
            artifacts.log(f"Starting {log_label}: {docket_numbers}")
            default_output_dir = artifacts.court_path(fallback_output_folder)
            artifacts.log(f"Counsel output folder(s): {', '.join(output_folders)}")

            def job_progress(percent, text, index=job_index, court=batch_label):
                overall = 10 + int(((index - 1) + (percent / 100)) / total_jobs * 85)
                progress_callback(min(overall, 95), f"{court}: {text}")

            try:
                _raise_if_cancelled(stop_event)
                checkbox_id = CIRCUIT_COURTS.get(court_label)
                if not checkbox_id:
                    message = f"No checkbox ID configured for: {court_label}"
                    artifacts.log(message, level="ERROR")
                    result["failed"] += 1
                    artifacts.add_record(progress_label, docket_numbers, status="Failed", message=message)
                    continue

                retrieval_attempts = 3
                job_result = {}
                for retrieval_attempt in range(1, retrieval_attempts + 1):
                    _raise_if_cancelled(stop_event)
                    if retrieval_attempt > 1:
                        artifacts.log(
                            f"Retrying {log_label} after CourtLink retrieval error "
                            f"(attempt {retrieval_attempt}/{retrieval_attempts}).",
                            level="WARNING",
                        )

                    search_attempts = 2
                    for search_attempt in range(1, search_attempts + 1):
                        _raise_if_cancelled(stop_event)
                        if search_attempt > 1:
                            artifacts.log(
                                f"Retrying {log_label} on a fresh CourtLink search form "
                                f"(attempt {search_attempt}/{search_attempts})."
                            )

                        try:
                            if _activate_reusable_search_form(driver, court_label, logger=artifacts):
                                job_progress(20, f"Selected Court: {court_label}")
                            else:
                                _close_extra_tabs(driver, logger=artifacts)
                                driver.get(LEXIS_SEARCH_URL)
                                job_progress(10, "Selecting court..")

                                if _court_is_already_selected(driver, court_label):
                                    artifacts.log(f"Reusing selected court: {court_label}")
                                    job_progress(20, f"Selected Court: {court_label}")
                                else:
                                    select_circuit_court_by_id(
                                        driver,
                                        checkbox_id,
                                        job_progress,
                                        logger=artifacts,
                                        stop_event=stop_event,
                                    )

                            submit_docket_search(driver, docket_numbers, logger=artifacts, stop_event=stop_event)
                            break
                        except (SearchDidNotLaunchError, TimeoutException, WebDriverException) as exc:
                            if search_attempt >= search_attempts:
                                raise
                            artifacts.log(
                                f"{log_label} search setup did not complete on attempt "
                                f"{search_attempt}/{search_attempts}; retrying. {_exception_summary(exc)}",
                                level="WARNING",
                            )
                            save_debug_snapshot(
                                driver,
                                f"{progress_label}_search_not_launched_attempt_{search_attempt}",
                                debug_dir=artifacts.debug_dir,
                                logger=artifacts,
                            )

                    job_progress(30, "Docket numbers submitted. Opening results...")

                    job_result = open_each_result(
                        driver,
                        job_progress,
                        output_dir=default_output_dir,
                        artifacts=artifacts,
                        court_label=court_label,
                        record_label=progress_label,
                        docket_numbers=docket_numbers,
                        output_folder_map=output_folder_map,
                        output_filename_map=output_filename_map,
                        default_output_folder=fallback_output_folder,
                        stop_event=stop_event,
                    )
                    if job_result.get("retrieval_error") and retrieval_attempt < retrieval_attempts:
                        artifacts.log(
                            f"CourtLink could not retrieve {docket_numbers}; retrying this docket only.",
                            level="WARNING",
                        )
                        continue
                    break

                if job_result.get("no_results") and artifacts:
                    artifacts.add_record(
                        job_result.get("record_label") or fallback_output_folder,
                        docket_numbers,
                        status="No Results",
                        message=job_result.get("message", "No result links found."),
                    )
                result["found"] += job_result.get("found", 0)
                result["saved"] += job_result.get("saved", 0)
                result["failed"] += job_result.get("failed", 0)
                result["no_results"] += job_result.get("no_results", 0)
                result["files"].extend(job_result.get("files", []))

            except RunCancelled:
                raise
            except Exception as e:
                message = f"{log_label} failed: {_exception_summary(e)}"
                artifacts.log(message, level="ERROR")
                result["failed"] += 1
                artifacts.add_record(progress_label, docket_numbers, status="Failed", message=message)
                try:
                    save_debug_snapshot(
                        driver,
                        f"{progress_label}_search_failed",
                        debug_dir=artifacts.debug_dir,
                        logger=artifacts,
                    )
                except Exception:
                    pass

        saved = result.get("saved", 0)
        found = result.get("found", 0)
        failed = result.get("failed", 0)
        no_results = result.get("no_results", 0)
        result["success"] = saved > 0 and failed == 0 and no_results == 0
        result["message"] = f"Saved {saved} of {found} result(s)."
        if failed or no_results:
            result["message"] += f" {failed} failed; {no_results} search batch(es) had no result or retrieval error."
        artifacts.log_run_summary(found, saved, failed, no_results)
        artifacts.log(result["message"])
        return result

    except RunCancelled:
        message = "Collection cancelled by user."
        result["message"] = message
        result["cancelled"] = True
        result["success"] = False
        artifacts.log(message, level="WARNING")
        artifacts.log_run_summary(
            result.get("found", 0),
            result.get("saved", 0),
            result.get("failed", 0),
            result.get("no_results", 0),
            cancelled=True,
        )
        return result

    except Exception as e:
        message = f"Error: {e}"
        artifacts.log(message, level="ERROR")
        result["message"] = message
        if result.get("failed", 0) == 0 and result.get("saved", 0) == 0:
            result["failed"] = 1
        artifacts.log_run_summary(
            result.get("found", 0),
            result.get("saved", 0),
            result.get("failed", 0),
            result.get("no_results", 0),
        )
        return result
    finally:
        progress_callback(100, "Closing browser...")
        if driver:
            driver.quit()
        artifacts.save_summary()
        if directory_copy_target:
            try:
                copied_paths = artifacts.copy_directory_run_outputs(directory_copy_target, output_folder_names)
                result["directory_copied_paths"] = copied_paths
                if copied_paths:
                    artifacts.log(
                        f"Copied counsel output folder(s) and Output workbook to source directory: {directory_copy_target}"
                    )
                else:
                    artifacts.log(
                        f"No counsel output folder was available to copy to source directory: {directory_copy_target}",
                        level="WARNING",
                    )
            except Exception as exc:
                result["directory_copy_error"] = str(exc)
                artifacts.log(f"Could not copy directory-run outputs: {exc}", level="ERROR")


def run_search(
    selected_court_label,
    docket_numbers,
    username,
    password,
    progress_callback=None,
    headless=True,
    stop_event=None,
    renamed_rows=None,
    directory_copy_target=None,
):
    return run_searches(
        {selected_court_label: docket_numbers},
        username,
        password,
        progress_callback=progress_callback,
        headless=headless,
        stop_event=stop_event,
        renamed_rows=renamed_rows,
        directory_copy_target=directory_copy_target,
    )
