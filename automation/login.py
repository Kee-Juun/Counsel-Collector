import logging

from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from automation.cancellation import raise_if_cancelled, sleep_with_cancel
from config.app_settings import LEXIS_HOME_URL, PROJECT_ROOT


def _is_displayed(driver, xpath):
    try:
        return driver.find_element(By.XPATH, xpath).is_displayed()
    except (NoSuchElementException, WebDriverException):
        return False


def LPCheck(driver, stop_event=None):
    limit_counter = 5
    loop_counter = 0

    while loop_counter < limit_counter:
        sleep_with_cancel(10, stop_event)
        raise_if_cancelled(stop_event)
        if _is_displayed(driver, '//*[@id="searchTerms"]'):
            print("User already signed in...")
            return True
        if _is_displayed(driver, '//*[@id="password"]'):
            print("Attempt login error...")
            print("Recheck credentials and rerun...")
            return False
        loop_counter += 1

    print("Error in accessing lexis plus, please rerun with caution!")
    return False


def _optional_text(driver, by, value):
    try:
        return driver.find_element(by, value).text.strip()
    except (NoSuchElementException, WebDriverException):
        return ""


def _save_login_screenshot(driver):
    output_path = PROJECT_ROOT / "login_debug.png"
    try:
        driver.save_screenshot(str(output_path))
        print(f"Saved login debug screenshot to: {output_path}")
    except WebDriverException as exc:
        logging.warning(f"Could not save login debug screenshot: {exc}")


def _report_login_state(driver, message):
    print(message)
    print(f"Current Lexis page: {driver.title} - {driver.current_url}")
    logging.warning(f"{message} Current page: {driver.title} - {driver.current_url}")
    _save_login_screenshot(driver)


def _click_if_present(driver, by, value, timeout=3):
    try:
        element = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, value)))
        try:
            element.click()
        except WebDriverException:
            driver.execute_script("arguments[0].click();", element)
        return True
    except (TimeoutException, WebDriverException):
        return False


def _dismiss_cookie_banner(driver):
    cookie_buttons = (
        (By.ID, "onetrust-accept-btn-handler"),
        (By.ID, "onetrust-reject-all-handler"),
        (By.CLASS_NAME, "onetrust-close-btn-handler"),
    )
    for by, value in cookie_buttons:
        if _click_if_present(driver, by, value):
            print("Dismissed Lexis cookie banner.")
            return True
    return False


def LPLogin(username, password, driver, stop_event=None):
    raise_if_cancelled(stop_event)
    driver.get(LEXIS_HOME_URL)
    max_attempts = 3

    for attempt in range(1, max_attempts + 1):
        raise_if_cancelled(stop_event)
        try:
            try:
                WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.ID, "searchTerms")))
                print("User already signed in...")
                return True
            except TimeoutException:
                pass

            raise_if_cancelled(stop_event)
            user_input = WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.ID, "userid")))
            _dismiss_cookie_banner(driver)
            logging.info("Signing in to Lexis Plus")
            user_input.clear()
            user_input.send_keys(username)
            _dismiss_cookie_banner(driver)
            raise_if_cancelled(stop_event)
            driver.find_element(By.ID, "signInSbmtBtn").click()

            try:
                password_input = WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.ID, "password")))
            except TimeoutException:
                error_text = _optional_text(driver, By.ID, "claimVerificationServerError")
                if error_text:
                    print(f"Lexis login error: {error_text}")
                _report_login_state(
                    driver,
                    "Password field did not appear. Lexis may be showing an SSO, MFA, browser-check, or changed login page.",
                )
                return False

            raise_if_cancelled(stop_event)
            password_input.clear()
            password_input.send_keys(password)
            password_input.send_keys(Keys.ENTER)
            return LPCheck(driver, stop_event=stop_event)

        except (TimeoutException, WebDriverException) as exc:
            raise_if_cancelled(stop_event)
            error_text = _optional_text(driver, By.ID, "claimVerificationServerError")
            if "User ID or password is invalid" in error_text:
                logging.error(f"Recheck credentials -- Login Failed: {error_text}")
                print(f"Recheck credentials -- Login Failed: {error_text}")
                return False

            logging.warning(f"Login attempt {attempt} failed: {exc}")
            if attempt == max_attempts:
                _report_login_state(driver, "Lexis login failed after all retry attempts.")
                return False

            logging.info("Trying Lexis login again.")

    return False
