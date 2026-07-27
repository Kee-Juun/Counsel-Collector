from pathlib import Path


APP_NAME = "Counsel Collector"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

RUNS_ROOT = Path.home() / "Downloads" / APP_NAME
RESULTS_DIR = RUNS_ROOT
DEBUG_DIR = PROJECT_ROOT / "debug"

LEXIS_HOME_URL = "https://advance.lexis.com/"
LEXIS_SEARCH_URL = "https://advance.lexis.com/courtlinksearch?crid=1dbf693a-19aa-4660-b855-bcdf53d7c013"

CIRCUIT_COURTS = {
    "1st Circuit Court": "33a3ef2a5b4cf8f1cafa5539f24b41dd/e2bb0d757eac5b62f8da255b70c652b3/93ec5b5cbf129e19e5026b27c07f69dd",
    "2nd Circuit Court": "33a3ef2a5b4cf8f1cafa5539f24b41dd/e2bb0d757eac5b62f8da255b70c652b3/56b12bf570eaf47a6309c055ead6998d",
    "3rd Circuit Court": "33a3ef2a5b4cf8f1cafa5539f24b41dd/e2bb0d757eac5b62f8da255b70c652b3/20b667d388bb26ebf6e3a67b424e64dc",
    "4th Circuit Court": "33a3ef2a5b4cf8f1cafa5539f24b41dd/e2bb0d757eac5b62f8da255b70c652b3/86d0be27b657d44b27fce29dc161d117",
    "5th Circuit Court": "33a3ef2a5b4cf8f1cafa5539f24b41dd/e2bb0d757eac5b62f8da255b70c652b3/f37e69d082f4ecc6e645b4593750ae6b",
    "6th Circuit Court": "33a3ef2a5b4cf8f1cafa5539f24b41dd/e2bb0d757eac5b62f8da255b70c652b3/9cc6a8814f03d8fdada1cc71748822cd",
    "7th Circuit Court": "33a3ef2a5b4cf8f1cafa5539f24b41dd/e2bb0d757eac5b62f8da255b70c652b3/c255010ca64df5bf560daa383fca4c3d",
    "8th Circuit Court": "33a3ef2a5b4cf8f1cafa5539f24b41dd/e2bb0d757eac5b62f8da255b70c652b3/175a9d377f40aa18c148dc0ac127a2ff",
    "9th Circuit Court": "33a3ef2a5b4cf8f1cafa5539f24b41dd/e2bb0d757eac5b62f8da255b70c652b3/bbbaf0cd2409c6b2ca0e22e37949382b",
    "10th Circuit Court": "33a3ef2a5b4cf8f1cafa5539f24b41dd/e2bb0d757eac5b62f8da255b70c652b3/748761d25cc6cdd1d923807e43d81f87",
    "11th Circuit Court": "33a3ef2a5b4cf8f1cafa5539f24b41dd/e2bb0d757eac5b62f8da255b70c652b3/71431b89eae828f02a412e5377a108c9",
    "DC Circuit Court": "33a3ef2a5b4cf8f1cafa5539f24b41dd/e2bb0d757eac5b62f8da255b70c652b3/1c9af8ea4b1a61cc04372fafd34b959f",
}

RESULT_LINK_SELECTORS = (
    "h2.doc-title > a[data-action='title']",
    "h2.doc-title a",
    "a[data-action='title']",
    "a[href*='document']",
)

NO_RESULT_PHRASES = (
    "no results",
    "no documents",
    "no cases",
    "no matching",
    "no search results",
    "did not match",
)

DOCUMENT_CONTENT_LOCATORS = (
    ("class", "doc-content"),
    ("id", "document-content"),
    ("id", "document-section"),
    ("css", ".SS_contentdocument"),
)
