import os
import re
from html import escape
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from config.app_settings import DOCUMENT_CONTENT_LOCATORS, RESULTS_DIR


def _emit(logger, message):
    if logger:
        logger.log(message)
    else:
        print(message)


def _by_from_kind(kind):
    if kind == "class":
        return By.CLASS_NAME
    if kind == "id":
        return By.ID
    if kind == "css":
        return By.CSS_SELECTOR
    raise ValueError(f"Unsupported locator kind: {kind}")


def find_document_content(driver):
    for kind, value in DOCUMENT_CONTENT_LOCATORS:
        try:
            elements = driver.find_elements(_by_from_kind(kind), value)
        except WebDriverException:
            continue
        for element in elements:
            try:
                if element.text.strip():
                    return element
            except WebDriverException:
                continue
    return None


def get_label_value(tag):
    sibling = tag.next_sibling
    while sibling:
        if isinstance(sibling, NavigableString):
            text = sibling.strip()
            if text and text != "\xa0":
                return text
        elif isinstance(sibling, Tag):
            text = sibling.get_text(strip=True)
            if text:
                return text
        sibling = sibling.next_sibling
    return ""


def _safe_case_number(value):
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value.strip("_") or "unknown"


def _next_available_path(path):
    if not path.exists():
        return path

    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def scrape_and_clean_lexis(
    driver,
    output_dir=None,
    logger=None,
    output_dir_resolver=None,
    output_filename_resolver=None,
):
    try:
        _emit(logger, "Waiting for document content to load...")
        content_element = WebDriverWait(driver, 45).until(lambda d: find_document_content(d))
        html_content = content_element.get_attribute("outerHTML")
        if not html_content.strip():
            raise Exception("Document section is empty after loading.")
        _emit(logger, "Saving counsel..")
    except Exception as e:
        message = f"Error retrieving document section: {e}"
        _emit(logger, message)
        return {"success": False, "error": message}

    soup = BeautifulSoup(html_content, "html.parser")

    for button in soup.find_all("button"):
        button.decompose()
    for tag in soup.find_all(["li", "span", "p", "div"]):
        if "â€¢" in tag.get_text():
            tag.string = tag.get_text().replace("â€¢", "")

    doc_title = soup.find("h1").text.strip() if soup.find("h1") else "Untitled"
    court_info = soup.find("p", class_="SS_DocumentInfo").text.strip() if soup.find("p", class_="SS_DocumentInfo") else ""
    retrieved_on = soup.find("span", class_="SS_Red").text.strip() if soup.find("span", class_="SS_Red") else ""
    header_section = soup.find("div", id="Header")
    participants_section = soup.find("div", id="Participants")

    content_parts = []

    content_parts.append(f"""
<p class="MsoNormal" align="center" style="margin-bottom:15.0pt;text-align:center;
line-height:normal;vertical-align:baseline"><b><span style="font-size:17.0pt;
font-family:&quot;inherit&quot;,serif">{escape(doc_title)}</span></b></p>
<p class="MsoNormal" align="center" style="margin-top:10.5pt;margin-right:0in;
margin-bottom:0in;margin-left:0in;text-align:center;line-height:normal;
vertical-align:baseline"><span style="font-family:&quot;inherit&quot;,serif;color:#6D6E71">{escape(court_info)}</span></p>
""")

    if retrieved_on:
        content_parts.append(f"""
<p class="MsoNormal" align="center" style="margin-bottom:0in;text-align:center;
line-height:normal;vertical-align:baseline"><b><span style="font-family:&quot;inherit&quot;,serif;
color:#212121">This case was retrieved on&nbsp;</span></b><b><span style="font-family:&quot;inherit&quot;,serif;color:#990033">{escape(retrieved_on)}</span></b></p>
""")

    content_parts.append("""<p class="MsoNormal" style="margin-bottom:0in;line-height:normal"><span style="font-size:10.5pt;font-family:&quot;Courier New&quot;;color:#212121"><br><br><br></span></p>""")
    content_parts.append("""
<div style="border:none;border-bottom:solid #009DDB 1.5pt;padding:0in 0in 3.0pt 0in">
<p class="MsoNormal" style="margin-bottom:0in;line-height:normal;vertical-align:
baseline;border:none;padding:0in"><span style="font-size:16.0pt;font-family:
&quot;inherit&quot;,serif;color:#212121">Header</span></p>
</div>
""")

    if header_section:
        for tag in header_section.find_all("span", class_="SS_bf"):
            label = tag.get_text(strip=True).rstrip(":")
            value = get_label_value(tag)

            content_parts.append(f"""
    <p class=MsoNormal style='margin-bottom:0in;line-height:normal;vertical-align:baseline'>
    <b><span style='font-size:10.5pt;font-family:"inherit",serif;color:#212121'>{escape(label)}:</span></b>
    <span style='font-size:10.5pt;font-family:"Courier New";color:#212121'> {escape(value)}</span></p>
    """)

    content_parts.append("<p class=MsoNormal><span style='font-size:10.5pt;font-family:\"Courier New\"'><br><br><br></span></p>")
    content_parts.append("""
<div style="border:none;border-bottom:solid #009DDB 1.5pt;padding:0in 0in 3.0pt 0in">
<p class="MsoNormal" style="margin-bottom:0in;line-height:normal;vertical-align:
baseline;border:none;padding:0in"><span style="font-size:16.0pt;font-family:
&quot;inherit&quot;,serif;color:#212121">Participants</span></p>
</div>
""")

    if participants_section:
        table = participants_section.find("table")
        if table:
            rows = table.find_all("tr")
            content_parts.append("""
<table border=0 cellspacing=0 cellpadding=0 width="100%" style='border-collapse:collapse'>
<tr>
<td width="50%" valign="top" style='padding:4.0pt;border:none'>
<b><span style='font-family:"inherit",serif'>Litigants</span></b></td>
<td width="50%" valign="top" style='padding:4.0pt;border:none'>
<b><span style='font-family:"inherit",serif'>Attorneys</span></b></td>
</tr>
""")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) == 2:
                    left = cells[0].decode_contents()
                    right = cells[1].decode_contents()
                    content_parts.append(f"""
<tr>
<td valign="bottom" style="padding:2.4pt 6.0pt 2.4pt 6.0pt">
<p class="MsoNormal" style="margin-bottom:0in;line-height:normal">
<span style="font-size:10.0pt;font-family:&quot;inherit&quot;,serif;color:#333333">{left}</span></p>
</td>
<td valign="bottom" style="padding:2.4pt 6.0pt 2.4pt 6.0pt">
<p class="MsoNormal" style="margin-bottom:0in;line-height:normal">
<span style="font-size:10.0pt;font-family:&quot;inherit&quot;,serif;color:#333333">{right}</span></p>
</td>
</tr>
""")
            content_parts.append("</table>")

    styled_html = f"""
<html>
<head>
<meta charset="utf-8">
<meta name=Generator content="USAP Counsel Extractor tool">
<style>
<!--
@font-face {{font-family:"Cambria Math";}}
@font-face {{font-family:Aptos;}}
@font-face {{font-family:inherit;}}
p.MsoNormal {{
    margin-top:0in;
    margin-right:0in;
    margin-bottom:8.0pt;
    margin-left:0in;
    line-height:115%;
    font-size:12.0pt;
    font-family:"Aptos",sans-serif;
}}
.MsoPapDefault {{
    margin-bottom:8.0pt;
    line-height:115%;
}}
@page WordSection1 {{
    size:8.5in 11.0in;
    margin:1.0in 1.0in 1.0in 1.0in;
}}
div.WordSection1 {{
    page:WordSection1;
}}
-->
</style>
</head>
<body lang=EN-US style='word-wrap:break-word'>
<div class=WordSection1>
{"".join(content_parts)}
</div>
</body>
</html>
"""

    case_number = "unknown"
    if header_section:
        for tag in header_section.find_all("span", class_="SS_bf"):
            label = tag.get_text(strip=True).lower()
            if "case number" in label:
                case_number = get_label_value(tag) or case_number
                break

    if output_dir_resolver:
        resolved_output_dir = output_dir_resolver(case_number)
        if resolved_output_dir:
            output_dir = resolved_output_dir

    output_dir = Path(output_dir) if output_dir else RESULTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = ""
    if output_filename_resolver:
        filename = str(output_filename_resolver(case_number) or "").strip()
    if not filename:
        filename = f"LDC_SMD_{_safe_case_number(case_number)}counsel.htm"
    filename = re.sub(r'[<>:"/\\|?*]+', "_", filename)
    if not filename.lower().endswith((".htm", ".html")):
        filename = f"{filename}.htm"
    output_path = _next_available_path(output_dir / filename)
    output_path.write_text(styled_html, encoding="utf-8")

    relative_path = Path(os.path.relpath(output_path, Path.cwd()))
    _emit(logger, f"Output saved to: {relative_path}")
    return {
        "success": True,
        "path": str(output_path),
        "case_number": case_number,
        "title": doc_title,
    }
