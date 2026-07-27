from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions


def session(headless=True):
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
    )
    options = ChromeOptions()
    options.page_load_strategy = "eager"
    options.add_argument(f"--user-agent={user_agent}")
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1365,900")
    options.add_argument("--log-level=3")
    options.add_argument("--disable-features=UseXNNPACK")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--ignore-ssl-errors")
    options.add_argument("start-maximized")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    return webdriver.Chrome(options=options)
