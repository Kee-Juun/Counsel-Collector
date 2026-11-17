from pathlib import Path
import re

from utils.docket_input import (
    ca_code_to_court_label,
    canonical_usap_code,
    counsel_filename_for_main_document,
    default_output_folder_for_court,
    docket_count,
    extract_docket_numbers,
    merge_dockets,
    normalize_dockets,
    output_folder_for_ca_code,
    output_folder_for_usap_code,
    unpublished_output_folder_for_court,
    usap_code_to_court_label,
)


CA_MARKER_RE = re.compile(
    r"(?:^|[_\-\s])(?P<code>CA(?:1[01]|[1-9])U?|CA[_-]?DCU?|CADCU?|DCU?)(?=[_\-\s.]|$)",
    re.IGNORECASE,
)
USAP_MARKER_RE = re.compile(
    r"(?:^|[_\-\s])(?P<code>USADCU|USAP(?:0?[1-9]|10|11|DCORD|DC)U?)(?=[_\-\s.]|$)",
    re.IGNORECASE,
)
UNPUBLISHED_MARKER_RE = re.compile(r"(?:^|[_\-\s])(?:UNPUB|UNPUBLISHED)(?=[_\-\s.]|$)", re.IGNORECASE)
PUBLISHED_MARKER_RE = re.compile(r"(?:^|[_\-\s])(?:PUB|PUBLISHED)(?=[_\-\s.]|$)", re.IGNORECASE)


def _path_label(path, root):
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _first_docket(filename):
    dockets = extract_docket_numbers(filename)
    return dockets[0] if dockets else ""


def _route_sources(path, root):
    yield path.name
    try:
        relative_parent = path.parent.relative_to(root)
        yield from reversed(relative_parent.parts)
    except ValueError:
        pass
    yield root.name


def _publication_hint(path, root):
    for source in _route_sources(path, root):
        ca_match = CA_MARKER_RE.search(source)
        if ca_match and ca_match.group("code").upper().replace("_", "").replace("-", "").endswith("U"):
            return "unpublished"
        if UNPUBLISHED_MARKER_RE.search(source):
            return "unpublished"
        if PUBLISHED_MARKER_RE.search(source):
            return "published"
    return ""


def _output_folder_for_hint(court_label, fallback, hint):
    if hint == "unpublished":
        return unpublished_output_folder_for_court(court_label)
    if hint == "published":
        return default_output_folder_for_court(court_label)
    return fallback


def _route_from_usap(path, root):
    for source in _route_sources(path, root):
        match = USAP_MARKER_RE.search(source)
        code = canonical_usap_code(match.group("code")) if match else ""
        if not code:
            continue

        court_label = usap_code_to_court_label(code)
        if not court_label:
            continue

        output_folder = output_folder_for_usap_code(code, path.name)
        if output_folder != unpublished_output_folder_for_court(court_label):
            output_folder = _output_folder_for_hint(court_label, output_folder, _publication_hint(path, root))
        return court_label, output_folder

    return "", ""


def _route_from_ca(path, root):
    for source in _route_sources(path, root):
        match = CA_MARKER_RE.search(source)
        if not match:
            continue

        court_label = ca_code_to_court_label(match.group("code"))
        if court_label:
            output_folder = _output_folder_for_hint(
                court_label,
                output_folder_for_ca_code(match.group("code")) or default_output_folder_for_court(court_label),
                _publication_hint(path, root),
            )
            return court_label, output_folder

    return "", ""


def _fallback_route(selected_courts, path, root):
    if len(selected_courts) != 1:
        return "", ""

    court_label = selected_courts[0]
    output_folder = _output_folder_for_hint(
        court_label,
        default_output_folder_for_court(court_label),
        _publication_hint(path, root),
    )
    return court_label, output_folder


def parse_pdf_directory(directory, selected_courts=None, include_existing_counsel=False):
    root = Path(directory)
    selected_courts = [court for court in (selected_courts or []) if court]
    files = sorted((path for path in root.rglob("*") if path.is_file()), key=lambda path: str(path).lower())

    counsel_dockets_by_route = {}
    for path in files:
        if "counsel" not in path.stem.lower():
            continue

        docket = _first_docket(path.name)
        if not docket:
            continue

        court_label, output_folder = _route_from_usap(path, root)
        if not court_label:
            court_label, output_folder = _route_from_ca(path, root)
        if not court_label:
            court_label, output_folder = _fallback_route(selected_courts, path, root)
        if court_label and output_folder:
            counsel_dockets_by_route.setdefault((court_label, output_folder), set()).add(docket)

    grouped = {}
    grouped_filenames = {}
    dockets_by_court = {}
    renamed_rows = []
    skipped_existing = []
    skipped_no_docket = []
    skipped_unknown_court = []
    pdf_count = 0

    for path in files:
        if path.suffix.lower() != ".pdf":
            continue
        if "counsel" in path.stem.lower():
            continue

        pdf_count += 1
        docket = _first_docket(path.name)
        label = _path_label(path, root)
        if not docket:
            skipped_no_docket.append(label)
            continue

        court_label, output_folder = _route_from_usap(path, root)
        if not court_label:
            court_label, output_folder = _route_from_ca(path, root)
        if not court_label:
            court_label, output_folder = _fallback_route(selected_courts, path, root)

        if not court_label:
            skipped_unknown_court.append(label)
            continue

        key = (court_label, output_folder)
        if not include_existing_counsel and docket in counsel_dockets_by_route.get(key, set()):
            skipped_existing.append(label)
            continue

        strip_dcord_flag = court_label == "DC Circuit Court" and output_folder == "USADCU"
        counsel_filename = counsel_filename_for_main_document(
            path.name,
            strip_dcord_unpublished_flag=strip_dcord_flag,
        )
        grouped[key] = merge_dockets(grouped.get(key, ""), docket)
        grouped_filenames.setdefault(key, {})
        grouped_filenames[key].setdefault(docket, counsel_filename)
        dockets_by_court[court_label] = merge_dockets(dockets_by_court.get(court_label, ""), docket)
        renamed_rows.append(
            {
                "COURT": output_folder,
                "FEEDBACK FORM FILENAME": path.name,
                "RENAMED": (
                    path.name
                    if not strip_dcord_flag
                    else re.sub(r"(?P<base>(?<!\d)\d{2,4}-\d{1,6})u(?=(_|\.|$))", r"\g<base>", path.name, flags=re.IGNORECASE)
                ),
            }
        )
        renamed_rows.append(
            {
                "COURT": output_folder,
                "FEEDBACK FORM FILENAME": "",
                "RENAMED": counsel_filename,
            }
        )

    jobs = [
        {
            "court_label": court_label,
            "docket_numbers": normalize_dockets(dockets),
            "output_folder": output_folder,
            "docket_output_filenames": grouped_filenames.get((court_label, output_folder), {}),
        }
        for (court_label, output_folder), dockets in grouped.items()
        if normalize_dockets(dockets)
    ]

    return {
        "root": str(root),
        "jobs": jobs,
        "dockets_by_court": dockets_by_court,
        "pdf_count": pdf_count,
        "queued_count": sum(docket_count(job["docket_numbers"]) for job in jobs),
        "skipped_existing": skipped_existing,
        "skipped_no_docket": skipped_no_docket,
        "skipped_unknown_court": skipped_unknown_court,
        "renamed_rows": renamed_rows,
    }
