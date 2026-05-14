import csv
import io
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
import streamlit as st
from bs4 import BeautifulSoup
from PIL import Image
from pyzbar.pyzbar import decode
from supabase import Client, create_client


# --- KONFIGURASYON VE BAGLANTI ---
st.set_page_config(page_title="MinisKitapApp", page_icon="📚", layout="wide")

APP_DIR = Path(__file__).resolve().parent
BRAND_IMAGE_PATH = APP_DIR / "assets" / "badger.png"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}

REQUEST_TIMEOUT = 8

PAGE_LABELS = {
    "library": "🏠 Kütüphanem",
    "add": "🔍 Kitap Ekle",
}

READING_STATUS_OPTIONS = [
    "Okunacak",
    "Okunuyor",
    "Okundu",
    "Yarım Bırakıldı",
    "Referans",
]

CATEGORY_OPTIONS = [
    "Kategorisiz",
    "Kişisel",
    "Profesyonel",
    "Roman",
    "Öykü",
    "Şiir",
    "Deneme",
    "Tarih",
    "Felsefe",
    "Psikoloji",
    "Bilim",
]

SORT_OPTIONS = [
    "Eser Adı (A-Z)",
    "Yazar Adı (A-Z)",
    "Yayınevi (A-Z)",
    "Yayın Yılı (Yeni-Eski)",
    "Yeni Eklenenler",
]

BOOK_FIELDS = [
    "isbn",
    "title",
    "author",
    "translator",
    "publisher",
    "page_count",
    "paper_type",
    "dimensions",
    "first_print_year",
    "print_edition",
    "language",
    "published_date",
    "genre",
    "description",
    "cover_url",
    "source_url",
]

SAVE_FIELDS = [
    "isbn",
    "title",
    "author",
    "translator",
    "publisher",
    "page_count",
    "paper_type",
    "dimensions",
    "first_print_year",
    "print_edition",
    "language",
    "published_date",
    "genre",
    "description",
    "cover_url",
    "source_url",
    "category",
    "reading_status",
    "favorite",
    "tags",
    "notes",
    "loaned_to",
    "rating",
]

TURKISH_RETAILERS = [
    ("D&R", "https://www.dr.com.tr/search?q={isbn}"),
    ("Kitapyurdu", "https://www.kitapyurdu.com/index.php?route=product/search&filter_name={isbn}"),
    ("BKM Kitap", "https://www.bkmkitap.com/arama?q={isbn}"),
    ("Kitapsepeti", "https://www.kitapsepeti.com/arama?q={isbn}"),
    ("Idefix", "https://www.idefix.com/search?q={isbn}"),
]


def get_secret(name: str, default: str = "") -> str:
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


@st.cache_resource
def init_connection() -> Client:
    url = get_secret("SUPABASE_URL")
    key = get_secret("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL veya SUPABASE_KEY secret olarak tanımlı değil.")
    return create_client(url, key)


try:
    supabase: Client = init_connection()
except Exception as exc:
    st.error(f"Supabase bağlantısı kurulamadı: {exc}")
    st.stop()


# --- GENEL YARDIMCILAR ---
def clean_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(clean_text(v) for v in value if clean_text(v))
    value = str(value)
    if "<" in value and ">" in value:
        value = BeautifulSoup(value, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", value).strip(" -|\n\t")


def blank_book(isbn: str = "") -> dict:
    book = {field: "" for field in BOOK_FIELDS}
    book["isbn"] = isbn
    book["category"] = "Kategorisiz"
    book["reading_status"] = "Okunacak"
    book["favorite"] = False
    book["tags"] = []
    book["notes"] = ""
    book["loaned_to"] = ""
    book["rating"] = None
    return book


def unique_keep_order(values) -> list:
    seen = set()
    result = []
    for value in values:
        if not value:
            continue
        key = str(value).casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def turkish_ascii(value: str) -> str:
    table = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    value = str(value or "").translate(table)
    value = unicodedata.normalize("NFKD", value)
    return value.encode("ascii", "ignore").decode("ascii")


def canonical_key(value: str) -> str:
    value = turkish_ascii(value).casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def dedupe_comma_values(value: str) -> str:
    parts = [clean_text(part) for part in re.split(r"[,;/|]+", str(value or ""))]
    parts = [part for part in parts if part]
    return ", ".join(unique_keep_order(parts))


def normalize_publisher_name(value: str) -> str:
    value = dedupe_comma_values(value)
    if not value:
        return ""

    aliases = {
        "alfa yayinlari": "Alfa Yayınları",
        "alfa yayincilik": "Alfa Yayınları",
        "turkiye is bankasi kultur yayinlari": "Türkiye İş Bankası Kültür Yayınları",
        "is bankasi kultur yayinlari": "Türkiye İş Bankası Kültür Yayınları",
        "yapi kredi yayinlari": "Yapı Kredi Yayınları",
        "can yayinlari": "Can Yayınları",
        "metis yayinlari": "Metis Yayınları",
        "iletisim yayinlari": "İletişim Yayınları",
        "pegasus yayinlari": "Pegasus Yayınları",
        "epsilon yayinlari": "Epsilon Yayınları",
        "dogan kitap": "Doğan Kitap",
        "kronik kitap": "Kronik Kitap",
    }

    normalized_parts = []
    for part in [p for p in value.split(", ") if p]:
        key = canonical_key(part)
        if key in aliases:
            normalized_parts.append(aliases[key])
        else:
            normalized_parts.append(part)
    return ", ".join(unique_keep_order(normalized_parts))


def parse_tags(value) -> list[str]:
    if isinstance(value, list):
        raw_tags = value
    else:
        raw_tags = re.split(r"[,;#]+", str(value or ""))
    tags = [clean_text(tag).lstrip("#") for tag in raw_tags]
    return unique_keep_order([tag for tag in tags if tag])


def tags_to_text(value) -> str:
    return ", ".join(parse_tags(value))


def safe_int(value, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def status_index(value: str) -> int:
    value = clean_text(value) or "Okunacak"
    if value in READING_STATUS_OPTIONS:
        return READING_STATUS_OPTIONS.index(value)
    return 0


def category_index(value: str) -> int:
    value = clean_text(value) or "Kategorisiz"
    if value in CATEGORY_OPTIONS:
        return CATEGORY_OPTIONS.index(value)
    return 0


def normalize_book_payload(data: dict) -> dict:
    normalized = dict(data)
    normalized["title"] = clean_text(normalized.get("title"))
    normalized["author"] = dedupe_comma_values(normalized.get("author"))
    normalized["translator"] = dedupe_comma_values(normalized.get("translator"))
    normalized["publisher"] = normalize_publisher_name(normalized.get("publisher"))
    normalized["page_count"] = clean_text(normalized.get("page_count"))
    normalized["paper_type"] = clean_text(normalized.get("paper_type"))
    normalized["dimensions"] = clean_text(normalized.get("dimensions"))
    normalized["first_print_year"] = clean_text(normalized.get("first_print_year"))
    normalized["print_edition"] = clean_text(normalized.get("print_edition"))
    normalized["language"] = clean_text(normalized.get("language"))
    normalized["published_date"] = clean_text(normalized.get("published_date"))
    normalized["genre"] = clean_text(normalized.get("genre"))
    normalized["description"] = clean_text(normalized.get("description"))
    normalized["cover_url"] = clean_text(normalized.get("cover_url"))
    normalized["source_url"] = clean_text(normalized.get("source_url"))
    normalized["category"] = clean_text(normalized.get("category")) or "Kategorisiz"
    normalized["reading_status"] = clean_text(normalized.get("reading_status")) or "Okunacak"
    normalized["favorite"] = bool(normalized.get("favorite"))
    normalized["tags"] = parse_tags(normalized.get("tags"))
    normalized["notes"] = clean_text(normalized.get("notes"))
    normalized["loaned_to"] = clean_text(normalized.get("loaned_to"))
    normalized["rating"] = normalized.get("rating") or None
    return {key: normalized.get(key) for key in SAVE_FIELDS if key in normalized}


def show_schema_error(exc: Exception):
    message = str(exc)
    if "column" in message.casefold() or "schema cache" in message.casefold():
        st.error(
            "Supabase tablosunda yeni kolonlar eksik görünüyor. "
            "Aşağıda verdiğim SQL migration dosyasını Supabase SQL Editor'da çalıştır."
        )
    else:
        st.error(f"İşlem başarısız: {exc}")


# --- ISBN NORMALIZASYONU ---
def only_isbn_chars(value: str) -> str:
    return re.sub(r"[^0-9Xx]", "", str(value or "")).upper()


def only_digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def isbn13_check_digit(first_12_digits: str) -> str:
    total = sum((1 if i % 2 == 0 else 3) * int(d) for i, d in enumerate(first_12_digits))
    return str((10 - (total % 10)) % 10)


def is_valid_isbn13(isbn: str) -> bool:
    isbn = only_digits(isbn)
    return (
        len(isbn) == 13
        and isbn.startswith(("978", "979"))
        and isbn13_check_digit(isbn[:12]) == isbn[-1]
    )


def is_valid_isbn10(isbn: str) -> bool:
    isbn = only_isbn_chars(isbn)
    if not re.fullmatch(r"\d{9}[\dX]", isbn):
        return False
    total = 0
    for i, char in enumerate(isbn):
        value = 10 if char == "X" else int(char)
        total += (10 - i) * value
    return total % 11 == 0


def isbn10_to_isbn13(isbn10: str) -> str:
    body = "978" + only_isbn_chars(isbn10)[:9]
    return body + isbn13_check_digit(body)


def isbn13_to_isbn10(isbn13: str) -> str:
    isbn13 = only_digits(isbn13)
    if not isbn13.startswith("978") or not is_valid_isbn13(isbn13):
        return ""
    body = isbn13[3:12]
    total = sum((10 - i) * int(d) for i, d in enumerate(body))
    check = (11 - (total % 11)) % 11
    return body + ("X" if check == 10 else str(check))


def normalize_isbn(raw_value: str) -> dict | None:
    raw_value = str(raw_value or "").strip()
    compact = only_isbn_chars(raw_value)
    digits = only_digits(raw_value)

    candidates = []
    if len(compact) in (10, 13):
        candidates.append(compact)
    candidates.extend(m.group(0) for m in re.finditer(r"97[89]\d{10}", digits))
    candidates.extend(m.group(0).upper() for m in re.finditer(r"\d{9}[\dXx]", compact))
    candidates = unique_keep_order(candidates)

    for candidate in candidates:
        if len(candidate) == 13 and is_valid_isbn13(candidate):
            isbn10 = isbn13_to_isbn10(candidate)
            return {
                "input": raw_value,
                "isbn13": candidate,
                "isbn10": isbn10,
                "variants": unique_keep_order([candidate, isbn10]),
                "valid": True,
            }
        if len(candidate) == 10 and is_valid_isbn10(candidate):
            isbn13 = isbn10_to_isbn13(candidate)
            return {
                "input": raw_value,
                "isbn13": isbn13,
                "isbn10": candidate,
                "variants": unique_keep_order([isbn13, candidate]),
                "valid": True,
            }

    for candidate in candidates:
        if len(candidate) == 13 and candidate.startswith(("978", "979")):
            return {
                "input": raw_value,
                "isbn13": candidate,
                "isbn10": "",
                "variants": [candidate],
                "valid": False,
            }
    return None


def first_year(value: str) -> str:
    match = re.search(r"(18|19|20)\d{2}", str(value or ""))
    return match.group(0) if match else ""


def pretty_language(value: str) -> str:
    value = clean_text(value)
    mapping = {
        "tr": "Türkçe",
        "tur": "Türkçe",
        "turkish": "Türkçe",
        "en": "İngilizce",
        "eng": "İngilizce",
        "english": "İngilizce",
    }
    return mapping.get(value.casefold(), value)


# --- HTTP YARDIMCILARI ---
def http_get_json(url: str, params: dict | None = None, headers: dict | None = None) -> dict | None:
    try:
        response = requests.get(
            url,
            params=params,
            headers={**HTTP_HEADERS, **(headers or {})},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        return None
    return None


def http_get_html(url: str) -> tuple[str, str]:
    try:
        response = requests.get(
            url,
            headers=HTTP_HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        if response.status_code == 200 and response.text:
            return response.url, response.text
    except Exception:
        pass
    return "", ""


def variant_in_text(variants: list[str], text: str) -> bool:
    compact = only_isbn_chars(text)
    return any(variant and variant in compact for variant in variants)


# --- KAYNAK 1: GOOGLE BOOKS ---
def google_record_from_item(item: dict, isbn: str, variants: list[str]) -> dict | None:
    info = item.get("volumeInfo", {})
    identifiers = [
        only_isbn_chars(identifier.get("identifier", ""))
        for identifier in info.get("industryIdentifiers", [])
    ]
    if identifiers and not any(identifier in variants for identifier in identifiers):
        return None

    title = clean_text(info.get("title"))
    subtitle = clean_text(info.get("subtitle"))
    if subtitle and subtitle.casefold() not in title.casefold():
        title = f"{title}: {subtitle}" if title else subtitle
    if not title:
        return None

    image_links = info.get("imageLinks", {}) or {}
    cover_url = image_links.get("thumbnail") or image_links.get("smallThumbnail") or ""
    cover_url = cover_url.replace("http://", "https://")

    book = blank_book(isbn)
    book.update(
        {
            "title": title,
            "author": clean_text(info.get("authors", [])),
            "publisher": normalize_publisher_name(info.get("publisher")),
            "published_date": clean_text(info.get("publishedDate")),
            "first_print_year": first_year(info.get("publishedDate")),
            "page_count": clean_text(info.get("pageCount")),
            "genre": clean_text(info.get("categories", [])),
            "language": pretty_language(info.get("language")),
            "description": clean_text(info.get("description")),
            "cover_url": cover_url,
            "source_url": clean_text(info.get("canonicalVolumeLink") or info.get("infoLink")),
            "_source": "Google Books",
        }
    )
    return book


def search_google_books(variants: list[str]) -> list[dict]:
    records = []
    for isbn in variants:
        data = http_get_json(
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": f"isbn:{isbn}", "maxResults": 5, "printType": "books"},
        )
        for item in (data or {}).get("items", []):
            record = google_record_from_item(item, variants[0], variants)
            if record:
                records.append(record)
    return records


# --- KAYNAK 2: OPEN LIBRARY ---
def openlibrary_author_name(author_key: str) -> str:
    if not author_key:
        return ""
    url = f"https://openlibrary.org{author_key}.json" if author_key.startswith("/") else author_key
    data = http_get_json(url)
    return clean_text((data or {}).get("name"))


def openlibrary_data_record(entry: dict, isbn: str) -> dict | None:
    title = clean_text(entry.get("title"))
    if not title:
        return None
    cover = entry.get("cover", {}) or {}
    book = blank_book(isbn)
    book.update(
        {
            "title": title,
            "author": clean_text([a.get("name") for a in entry.get("authors", [])]),
            "publisher": normalize_publisher_name([p.get("name") for p in entry.get("publishers", [])]),
            "published_date": clean_text(entry.get("publish_date")),
            "first_print_year": first_year(entry.get("publish_date")),
            "page_count": clean_text(entry.get("number_of_pages") or entry.get("pagination")),
            "genre": clean_text([s.get("name") for s in entry.get("subjects", [])[:5]]),
            "cover_url": cover.get("large") or cover.get("medium") or cover.get("small") or "",
            "source_url": clean_text(entry.get("url")),
            "_source": "Open Library Books API",
        }
    )
    return book


def openlibrary_edition_record(edition: dict, isbn: str) -> dict | None:
    title = clean_text(edition.get("title"))
    if not title:
        return None

    author_names = []
    for author in edition.get("authors", [])[:3]:
        name = openlibrary_author_name(author.get("key", ""))
        if name:
            author_names.append(name)

    languages = []
    for lang in edition.get("languages", []):
        key = clean_text(lang.get("key", "")).split("/")[-1]
        if key:
            languages.append(pretty_language(key))

    book = blank_book(isbn)
    book.update(
        {
            "title": title,
            "author": clean_text(author_names),
            "publisher": normalize_publisher_name(edition.get("publishers", [])),
            "published_date": clean_text(edition.get("publish_date")),
            "first_print_year": first_year(edition.get("publish_date")),
            "page_count": clean_text(edition.get("number_of_pages") or edition.get("pagination")),
            "dimensions": clean_text(edition.get("physical_dimensions")),
            "language": clean_text(languages),
            "cover_url": f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false",
            "source_url": f"https://openlibrary.org/isbn/{isbn}",
            "_source": "Open Library ISBN",
        }
    )
    return book


def openlibrary_search_record(doc: dict, isbn: str, variants: list[str]) -> dict | None:
    doc_isbns = [only_isbn_chars(v) for v in doc.get("isbn", [])]
    if doc_isbns and not any(variant in doc_isbns for variant in variants):
        return None
    title = clean_text(doc.get("title"))
    if not title:
        return None
    cover_id = doc.get("cover_i")
    book = blank_book(isbn)
    book.update(
        {
            "title": title,
            "author": clean_text(doc.get("author_name", [])),
            "publisher": normalize_publisher_name((doc.get("publisher") or [])[:2]),
            "first_print_year": clean_text(doc.get("first_publish_year")),
            "page_count": clean_text(doc.get("number_of_pages_median")),
            "genre": clean_text((doc.get("subject") or [])[:5]),
            "language": pretty_language((doc.get("language") or [""])[0]),
            "cover_url": f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg" if cover_id else "",
            "source_url": f"https://openlibrary.org{doc.get('key', '')}" if doc.get("key") else "",
            "_source": "Open Library Search",
        }
    )
    return book


def search_openlibrary(variants: list[str]) -> list[dict]:
    records = []
    for isbn in variants:
        data = http_get_json(
            "https://openlibrary.org/api/books",
            params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
        )
        entry = (data or {}).get(f"ISBN:{isbn}")
        if entry:
            record = openlibrary_data_record(entry, variants[0])
            if record:
                records.append(record)

        edition = http_get_json(f"https://openlibrary.org/isbn/{isbn}.json")
        if edition:
            record = openlibrary_edition_record(edition, variants[0])
            if record:
                records.append(record)

        search = http_get_json(
            "https://openlibrary.org/search.json",
            params={
                "isbn": isbn,
                "limit": 5,
                "fields": (
                    "title,author_name,publisher,first_publish_year,"
                    "number_of_pages_median,isbn,cover_i,language,subject,key"
                ),
            },
        )
        for doc in (search or {}).get("docs", []):
            record = openlibrary_search_record(doc, variants[0], variants)
            if record:
                records.append(record)
    return records


# --- OPSIYONEL KAYNAK 3: ISBNDB ---
def search_isbndb(variants: list[str]) -> list[dict]:
    api_key = get_secret("ISBNDB_API_KEY")
    if not api_key:
        return []

    records = []
    headers = {"Authorization": api_key, "x-api-key": api_key}
    for isbn in variants:
        for base_url in ("https://api2.isbndb.com/book/", "https://api.isbndb.com/book/"):
            data = http_get_json(base_url + isbn, headers=headers)
            if not data:
                continue
            book_data = data.get("book", data)
            title = clean_text(book_data.get("title_long") or book_data.get("title"))
            if not title:
                continue
            book = blank_book(variants[0])
            book.update(
                {
                    "title": title,
                    "author": clean_text(book_data.get("authors", [])),
                    "publisher": normalize_publisher_name(book_data.get("publisher")),
                    "published_date": clean_text(book_data.get("date_published")),
                    "first_print_year": first_year(book_data.get("date_published")),
                    "page_count": clean_text(book_data.get("pages")),
                    "dimensions": clean_text(book_data.get("dimensions")),
                    "language": pretty_language(book_data.get("language")),
                    "genre": clean_text(book_data.get("subjects", [])),
                    "description": clean_text(book_data.get("overview") or book_data.get("synopsys")),
                    "cover_url": clean_text(book_data.get("image")),
                    "source_url": f"https://isbndb.com/book/{isbn}",
                    "_source": "ISBNdb",
                }
            )
            records.append(book)
            break
    return records


# --- SON SANS: TURKCE SITE HTML / JSON-LD OKUMA ---
def image_to_url(value) -> str:
    if isinstance(value, list) and value:
        return image_to_url(value[0])
    if isinstance(value, dict):
        return clean_text(value.get("url") or value.get("contentUrl") or value.get("@id"))
    return clean_text(value)


def jsonld_objects(soup: BeautifulSoup) -> list[dict]:
    objects = []

    def walk(value):
        if isinstance(value, dict):
            objects.append(value)
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for script in soup.find_all("script", type=lambda t: t and "ld+json" in t):
        raw = (script.string or script.get_text() or "").strip()
        if not raw:
            continue
        try:
            walk(json.loads(raw))
        except Exception:
            continue
    return objects


def person_or_org_to_text(value) -> str:
    if isinstance(value, list):
        return clean_text([person_or_org_to_text(item) for item in value])
    if isinstance(value, dict):
        return clean_text(value.get("name") or value.get("@id"))
    return clean_text(value)


def record_from_jsonld(obj: dict, variants: list[str], source: str, url: str) -> dict | None:
    type_value = obj.get("@type", "")
    type_text = " ".join(type_value) if isinstance(type_value, list) else str(type_value)
    looks_like_book = any(token in type_text.casefold() for token in ("book", "product"))
    has_isbn_field = any(key in obj for key in ("isbn", "gtin13", "sku", "productID"))
    if not looks_like_book and not has_isbn_field:
        return None

    serialized = json.dumps(obj, ensure_ascii=False)
    if has_isbn_field and not variant_in_text(variants, serialized):
        return None

    title = clean_text(obj.get("name") or obj.get("headline"))
    if not title:
        return None

    book = blank_book(variants[0])
    book.update(
        {
            "title": title,
            "author": person_or_org_to_text(obj.get("author") or obj.get("brand")),
            "publisher": normalize_publisher_name(person_or_org_to_text(obj.get("publisher"))),
            "description": clean_text(obj.get("description")),
            "cover_url": image_to_url(obj.get("image")),
            "source_url": url,
            "_source": source,
        }
    )
    return book


def meta_content(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", property=name) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return clean_text(tag["content"])
    return ""


def line_value(lines: list[str], labels: list[str]) -> str:
    label_set = [label.casefold() for label in labels]
    blocked_next_words = {
        "yazar",
        "yayınevi",
        "yayıncı",
        "çevirmen",
        "sayfa sayısı",
        "hamur tipi",
        "ebat",
        "baskı sayısı",
        "dil",
    }
    for index, line in enumerate(lines):
        folded = line.casefold()
        for label, folded_label in zip(labels, label_set):
            if folded.startswith(folded_label):
                value = clean_text(line[len(label):].strip(" :;-"))
                if value and value.casefold() not in blocked_next_words and len(value) < 150:
                    return value
                if index + 1 < len(lines):
                    next_line = clean_text(lines[index + 1])
                    if next_line and next_line.casefold() not in blocked_next_words and len(next_line) < 150:
                        return next_line
    return ""


def extract_book_from_html(html: str, variants: list[str], source: str, url: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    records = []

    for obj in jsonld_objects(soup):
        record = record_from_jsonld(obj, variants, source, url)
        if record:
            records.append(record)

    page_has_isbn = variant_in_text(variants, soup.get_text(" "))
    if records and page_has_isbn:
        return max(records, key=score_record)
    if records:
        return max(records, key=score_record)
    if not page_has_isbn:
        return None

    title = (
        clean_text(meta_content(soup, "og:title", "twitter:title"))
        or clean_text(soup.find("h1").get_text(" ") if soup.find("h1") else "")
        or clean_text(soup.title.get_text(" ") if soup.title else "")
    )
    for separator in (" | ", " - "):
        if separator in title and source.casefold() in title.casefold():
            title = clean_text(title.split(separator)[0])

    if not title or title.casefold() in {"arama", "search"}:
        return None

    lines = [clean_text(line) for line in soup.get_text("\n").splitlines()]
    lines = [line for line in lines if line]

    book = blank_book(variants[0])
    book.update(
        {
            "title": title,
            "author": line_value(lines, ["Yazar", "Yazarlar"]),
            "translator": line_value(lines, ["Çevirmen", "Cevirmen"]),
            "publisher": normalize_publisher_name(
                line_value(lines, ["Yayınevi", "Yayıncı", "Yayinevi", "Publisher"])
            ),
            "page_count": line_value(lines, ["Sayfa Sayısı", "Sayfa", "Pages"]),
            "paper_type": line_value(lines, ["Hamur Tipi", "Kağıt Cinsi", "Kagit Cinsi"]),
            "dimensions": line_value(lines, ["Ebat", "Boyut", "Kitap Boyutu"]),
            "first_print_year": first_year(line_value(lines, ["İlk Baskı Yılı", "Basım Yılı", "Yayın Tarihi"])),
            "print_edition": line_value(lines, ["Baskı Sayısı", "Baski Sayisi"]),
            "language": pretty_language(line_value(lines, ["Dil", "Kitap Dili", "Basım Dili"])),
            "cover_url": meta_content(soup, "og:image", "twitter:image"),
            "source_url": url,
            "_source": source,
        }
    )
    return book if book["title"] else None


def candidate_product_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    links = []
    bad_tokens = ("sepet", "cart", "login", "uye", "üyelik", "arama", "search", "javascript:", "mailto:")
    good_tokens = ("kitap", "urun", "ürün", "product", "/p/", "p-")

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        full_url = urljoin(base_url, href)
        folded = full_url.casefold()
        if any(token in folded for token in bad_tokens):
            continue
        link_text = clean_text(anchor.get_text(" "))
        if any(token in folded for token in good_tokens) or len(link_text) > 3:
            if full_url not in links:
                links.append(full_url)
        if len(links) >= 5:
            break
    return links


def search_turkish_retailers(variants: list[str]) -> list[dict]:
    records = []
    for source, template in TURKISH_RETAILERS:
        source_found = False
        for isbn in variants:
            search_url = template.format(isbn=isbn)
            final_url, html = http_get_html(search_url)
            if not html:
                continue

            record = extract_book_from_html(html, variants, source, final_url)
            if record:
                records.append(record)
                source_found = True
                break

            soup = BeautifulSoup(html, "html.parser")
            for link in candidate_product_links(soup, final_url)[:3]:
                product_url, product_html = http_get_html(link)
                if not product_html:
                    continue
                record = extract_book_from_html(product_html, variants, source, product_url)
                if record:
                    records.append(record)
                    source_found = True
                    break
            if source_found:
                break
    return records


# --- KAYITLARI BIRLESTIRME ---
def score_record(record: dict) -> int:
    weights = {
        "title": 10,
        "author": 6,
        "publisher": 5,
        "page_count": 3,
        "first_print_year": 2,
        "translator": 2,
        "language": 1,
        "genre": 1,
        "cover_url": 2,
        "description": 1,
    }
    return sum(weight for field, weight in weights.items() if clean_text(record.get(field)))


def merge_records(normalized: dict, records: list[dict]) -> dict | None:
    usable = [record for record in records if clean_text(record.get("title"))]
    if not usable:
        return None

    ranked = sorted(usable, key=score_record, reverse=True)
    merged = blank_book(normalized.get("isbn13") or normalized["variants"][0])

    for record in ranked:
        for field in BOOK_FIELDS:
            if field == "isbn":
                continue
            value = clean_text(record.get(field))
            if value and not merged.get(field):
                merged[field] = value

    merged["publisher"] = normalize_publisher_name(merged.get("publisher"))
    sources = unique_keep_order(clean_text(record.get("_source")) for record in ranked)
    merged["_sources"] = ", ".join(sources)
    merged["_score"] = score_record(merged)
    return merged


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def get_book_info_comprehensive(raw_isbn: str) -> dict:
    normalized = normalize_isbn(raw_isbn)
    if not normalized:
        return {
            "ok": False,
            "error": "Geçerli bir ISBN-10 veya ISBN-13 bulunamadı.",
            "normalized": None,
            "book": blank_book(only_digits(raw_isbn)),
            "records_count": 0,
        }

    variants = normalized["variants"]
    records = []
    records.extend(search_google_books(variants))
    records.extend(search_openlibrary(variants))
    records.extend(search_isbndb(variants))

    merged = merge_records(normalized, records)
    if not merged or merged["_score"] < 18 or not merged.get("publisher") or not merged.get("page_count"):
        records.extend(search_turkish_retailers(variants))
        merged = merge_records(normalized, records)

    if merged:
        return {
            "ok": True,
            "error": "",
            "normalized": normalized,
            "book": merged,
            "records_count": len(records),
        }

    return {
        "ok": False,
        "error": "Online kaynaklarda güvenilir kitap kaydı bulunamadı.",
        "normalized": normalized,
        "book": blank_book(normalized.get("isbn13") or variants[0]),
        "records_count": len(records),
    }


# --- VERITABANI YARDIMCILARI ---
def fetch_books() -> list[dict]:
    try:
        response = supabase.table("books").select("*").execute()
        return response.data or []
    except Exception as exc:
        st.error(f"Kitaplar yüklenemedi: {exc}")
        return []


def insert_book(data: dict) -> bool:
    payload = normalize_book_payload(data)
    try:
        supabase.table("books").insert(payload).execute()
        return True
    except Exception as exc:
        show_schema_error(exc)
        return False


def update_book(book_id, data: dict) -> bool:
    payload = normalize_book_payload(data)
    try:
        supabase.table("books").update(payload).eq("id", book_id).execute()
        return True
    except Exception as exc:
        show_schema_error(exc)
        return False


def delete_book(book_id) -> bool:
    try:
        supabase.table("books").delete().eq("id", book_id).execute()
        return True
    except Exception as exc:
        st.error(f"Silme işlemi başarısız: {exc}")
        return False


def isbn_exists(isbn: str) -> dict | None:
    isbn = clean_text(isbn)
    if not isbn:
        return None
    try:
        data = (
            supabase.table("books")
            .select("id,title,isbn")
            .eq("isbn", isbn)
            .limit(1)
            .execute()
            .data
        )
        return data[0] if data else None
    except Exception:
        return None


# --- FILTRE, SIRALAMA VE YEDEK ---
def all_tags(books: list[dict]) -> list[str]:
    tags = []
    for book in books:
        tags.extend(parse_tags(book.get("tags")))
    return sorted(unique_keep_order(tags), key=canonical_key)


def book_matches_search(book: dict, term: str) -> bool:
    term = canonical_key(term)
    if not term:
        return True
    haystack = " ".join(
        [
            clean_text(book.get("title")),
            clean_text(book.get("author")),
            clean_text(book.get("publisher")),
            clean_text(book.get("isbn")),
            tags_to_text(book.get("tags")),
        ]
    )
    return term in canonical_key(haystack)


def filter_books(
    books: list[dict],
    search_term: str,
    personal_filter: str,
    status_filter: str,
    tag_filter: str,
) -> list[dict]:
    filtered = []
    for book in books:
        if not book_matches_search(book, search_term):
            continue

        status = clean_text(book.get("reading_status")) or "Okunacak"
        if status_filter != "Tümü" and status != status_filter:
            continue

        if personal_filter == "Favoriler" and not book.get("favorite"):
            continue
        if personal_filter == "Ödünç Verilenler" and not clean_text(book.get("loaned_to")):
            continue
        if personal_filter == "Notu Olanlar" and not clean_text(book.get("notes")):
            continue

        if tag_filter != "Tüm Etiketler" and tag_filter not in parse_tags(book.get("tags")):
            continue

        filtered.append(book)
    return filtered


def sort_books(books: list[dict], sort_by: str) -> list[dict]:
    if sort_by == "Yazar Adı (A-Z)":
        return sorted(books, key=lambda b: canonical_key(b.get("author") or b.get("title")))
    if sort_by == "Yayınevi (A-Z)":
        return sorted(books, key=lambda b: canonical_key(b.get("publisher") or b.get("title")))
    if sort_by == "Yayın Yılı (Yeni-Eski)":
        return sorted(books, key=lambda b: safe_int(first_year(b.get("first_print_year")), 0), reverse=True)
    if sort_by == "Yeni Eklenenler":
        return sorted(books, key=lambda b: clean_text(b.get("created_at")), reverse=True)
    return sorted(books, key=lambda b: canonical_key(b.get("title")))


def books_to_csv(books: list[dict]) -> str:
    if not books:
        return ""
    preferred = [
        "id",
        "isbn",
        "title",
        "author",
        "publisher",
        "translator",
        "page_count",
        "first_print_year",
        "reading_status",
        "category",
        "favorite",
        "tags",
        "rating",
        "loaned_to",
        "notes",
        "cover_url",
        "source_url",
        "created_at",
        "updated_at",
    ]
    extra = sorted({key for book in books for key in book.keys()} - set(preferred))
    fields = [field for field in preferred if any(field in book for book in books)] + extra
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for book in books:
        row = dict(book)
        row["tags"] = tags_to_text(row.get("tags"))
        writer.writerow(row)
    return buffer.getvalue()


def backup_json(books: list[dict]) -> str:
    payload = {
        "app": "MinisKitapApp",
        "schema_version": 2,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "book_count": len(books),
        "books": books,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


# --- UI YARDIMCILARI ---
def inject_css():
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.4rem; }
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { margin-bottom: 0.25rem; }
        .book-meta {
            color: rgba(250, 250, 250, 0.72);
            font-size: 0.95rem;
            margin: 0.1rem 0 0.8rem 0;
        }
        .status-pill {
            display: inline-block;
            padding: 0.2rem 0.55rem;
            border: 1px solid rgba(250, 250, 250, 0.18);
            border-radius: 999px;
            margin-right: 0.35rem;
            font-size: 0.86rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def set_page(page: str):
    st.session_state["page"] = page


def render_top_bar():
    left, right = st.columns([0.82, 0.18], vertical_alignment="center")
    with left:
        if BRAND_IMAGE_PATH.exists():
            st.image(str(BRAND_IMAGE_PATH), width=170)
        else:
            st.title("MinisKitapApp")
    with right:
        st.write("")
        if st.button("➕ Kitap Ekle", use_container_width=True, key="top_add_book"):
            set_page("add")
            st.rerun()


def render_sidebar(all_books: list[dict]):
    st.sidebar.title("📚 MinisKitapApp")

    current = st.session_state.get("page", "library")
    labels = list(PAGE_LABELS.values())
    reverse_labels = {value: key for key, value in PAGE_LABELS.items()}
    selected_label = st.sidebar.radio(
        "Menü",
        labels,
        index=labels.index(PAGE_LABELS.get(current, PAGE_LABELS["library"])),
    )
    st.session_state["page"] = reverse_labels[selected_label]

    st.sidebar.divider()
    st.sidebar.subheader("Kişisel Filtreler")
    personal_filter = st.sidebar.selectbox(
        "Görünüm",
        ["Tümü", "Favoriler", "Ödünç Verilenler", "Notu Olanlar"],
        key="personal_filter",
    )
    status_filter = st.sidebar.selectbox(
        "Okunma Durumu",
        ["Tümü", *READING_STATUS_OPTIONS],
        key="status_filter",
    )
    tag_options = ["Tüm Etiketler", *all_tags(all_books)]
    tag_filter = st.sidebar.selectbox("Etiket", tag_options, key="tag_filter")

    st.sidebar.subheader("Kitap Filtreleri")
    search_term = st.sidebar.text_input("Kitap adı, yazar, yayınevi veya ISBN ara", key="library_search")
    sort_by = st.sidebar.selectbox("Sıralama", SORT_OPTIONS, key="sort_by")

    st.sidebar.divider()
    st.sidebar.subheader("Yedek")
    backup_name = f"miniskitapapp_yedek_{datetime.now().strftime('%Y%m%d_%H%M')}"
    st.sidebar.download_button(
        "Kitaplığımın yedek bilgilerini al (JSON)",
        data=backup_json(all_books),
        file_name=f"{backup_name}.json",
        mime="application/json",
        use_container_width=True,
    )
    st.sidebar.download_button(
        "CSV olarak indir",
        data=books_to_csv(all_books),
        file_name=f"{backup_name}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    return personal_filter, status_filter, tag_filter, search_term, sort_by


def render_cover(cover_url: str, width: int = 120):
    cover_url = clean_text(cover_url)
    if cover_url:
        st.image(cover_url, width=width)
    else:
        st.caption("Kapak yok")


def decode_isbn_from_image(image_file) -> str:
    if not image_file:
        return ""
    try:
        image = Image.open(image_file).convert("RGB")
        decoded_items = decode(image)
        for item in decoded_items:
            candidate = item.data.decode("utf-8", errors="ignore").strip()
            if normalize_isbn(candidate):
                return candidate
    except Exception:
        return ""
    return ""


def book_title(book: dict) -> str:
    return clean_text(book.get("title")) or "İsimsiz Kitap"


def book_author(book: dict) -> str:
    return clean_text(book.get("author")) or "Yazar Bilinmiyor"


def status_badges(book: dict):
    status = clean_text(book.get("reading_status")) or "Okunacak"
    tags = parse_tags(book.get("tags"))
    favorite = "Favori" if book.get("favorite") else ""
    pieces = [status, favorite, *tags[:4]]
    html = "".join(f'<span class="status-pill">{piece}</span>' for piece in pieces if piece)
    st.markdown(html, unsafe_allow_html=True)


def build_form_data(prefix: str, initial: dict) -> dict:
    initial_tags = tags_to_text(initial.get("tags"))
    c1, c2 = st.columns(2)
    with c1:
        title = st.text_input("Kitap Adı*", value=clean_text(initial.get("title")), key=f"{prefix}_title")
        author = st.text_input("Yazar", value=clean_text(initial.get("author")), key=f"{prefix}_author")
        translator = st.text_input("Çevirmen", value=clean_text(initial.get("translator")), key=f"{prefix}_translator")
        publisher = st.text_input("Yayınevi", value=clean_text(initial.get("publisher")), key=f"{prefix}_publisher")
        first_print_year = st.text_input(
            "Yayın / Baskı Yılı",
            value=clean_text(initial.get("first_print_year")),
            key=f"{prefix}_year",
        )
        page_count = st.text_input("Sayfa Sayısı", value=clean_text(initial.get("page_count")), key=f"{prefix}_pages")
    with c2:
        reading_status = st.selectbox(
            "Okunma Durumu",
            READING_STATUS_OPTIONS,
            index=status_index(initial.get("reading_status")),
            key=f"{prefix}_status",
        )
        category = st.selectbox(
            "Kategori",
            CATEGORY_OPTIONS,
            index=category_index(initial.get("category")),
            key=f"{prefix}_category",
        )
        tags = st.text_input("Etiketler", value=initial_tags, key=f"{prefix}_tags")
        favorite = st.checkbox("Favori", value=bool(initial.get("favorite")), key=f"{prefix}_favorite")
        rating = st.slider(
            "Puan",
            min_value=0,
            max_value=5,
            value=safe_int(initial.get("rating"), 0),
            key=f"{prefix}_rating",
        )
        loaned_to = st.text_input("Ödünç Verilen Kişi", value=clean_text(initial.get("loaned_to")), key=f"{prefix}_loaned")

    c3, c4 = st.columns(2)
    with c3:
        paper_type = st.text_input("Hamur Tipi", value=clean_text(initial.get("paper_type")), key=f"{prefix}_paper")
        dimensions = st.text_input("Ebat", value=clean_text(initial.get("dimensions")), key=f"{prefix}_dimensions")
        print_edition = st.text_input("Baskı Sayısı", value=clean_text(initial.get("print_edition")), key=f"{prefix}_edition")
    with c4:
        language = st.text_input("Dil", value=clean_text(initial.get("language")), key=f"{prefix}_language")
        genre = st.text_input("Tür / Konu", value=clean_text(initial.get("genre")), key=f"{prefix}_genre")
        cover_url = st.text_input("Kapak Görseli URL", value=clean_text(initial.get("cover_url")), key=f"{prefix}_cover")

    notes = st.text_area("Notlar", value=clean_text(initial.get("notes")), key=f"{prefix}_notes")

    return {
        "isbn": clean_text(initial.get("isbn")),
        "title": title,
        "author": author,
        "translator": translator,
        "publisher": publisher,
        "page_count": page_count,
        "paper_type": paper_type,
        "dimensions": dimensions,
        "first_print_year": first_print_year,
        "print_edition": print_edition,
        "language": language,
        "published_date": clean_text(initial.get("published_date")),
        "genre": genre,
        "description": clean_text(initial.get("description")),
        "cover_url": cover_url,
        "source_url": clean_text(initial.get("source_url")),
        "category": category,
        "reading_status": reading_status,
        "favorite": favorite,
        "tags": parse_tags(tags),
        "notes": notes,
        "loaned_to": loaned_to,
        "rating": rating or None,
    }


def render_book_details(book: dict):
    cover_col, info_col, more_col = st.columns([0.13, 0.47, 0.40])
    with cover_col:
        render_cover(book.get("cover_url"), width=105)
    with info_col:
        status_badges(book)
        st.write(f"**ISBN:** {book.get('isbn') or '-'}")
        st.write(f"**Yazar:** {book_author(book)}")
        st.write(f"**Yayınevi:** {book.get('publisher') or '-'}")
        st.write(f"**Yayın Yılı:** {book.get('first_print_year') or '-'}")
        st.write(f"**Sayfa Sayısı:** {book.get('page_count') or '-'}")
    with more_col:
        st.write(f"**Çevirmen:** {book.get('translator') or '-'}")
        st.write(f"**Kategori:** {book.get('category') or 'Kategorisiz'}")
        st.write(f"**Puan:** {book.get('rating') or '-'}")
        st.write(f"**Ödünç:** {book.get('loaned_to') or '-'}")
        if clean_text(book.get("notes")):
            st.write(f"**Not:** {book.get('notes')}")


def render_book_editor(book: dict):
    book_id = book.get("id")
    with st.form(f"edit_book_{book_id}"):
        data = build_form_data(f"edit_{book_id}", book)
        submitted = st.form_submit_button("Değişiklikleri Kaydet", use_container_width=True)

    if submitted:
        if not clean_text(data.get("title")):
            st.error("Kitap adı zorunlu.")
        elif update_book(book_id, data):
            st.success("Kitap bilgileri güncellendi.")
            st.rerun()

    danger_left, danger_right = st.columns([0.78, 0.22])
    with danger_right:
        if st.button("🗑️ Sil", key=f"delete_{book_id}", use_container_width=True):
            if delete_book(book_id):
                st.success("Kitap silindi.")
                st.rerun()


def render_library_page(all_books: list[dict], filters: tuple):
    personal_filter, status_filter, tag_filter, search_term, sort_by = filters
    st.header("Kitaplığım")

    filtered = filter_books(all_books, search_term, personal_filter, status_filter, tag_filter)
    books = sort_books(filtered, sort_by)

    total = len(all_books)
    st.caption(f"{len(books)} kitap gösteriliyor · toplam {total} kitap")

    if not books:
        st.info("Bu filtrelerde kitap bulunamadı.")
        return

    for book in books:
        title = book_title(book)
        author = book_author(book)
        status = clean_text(book.get("reading_status")) or "Okunacak"
        with st.expander(f"📖 {title} - {author} · {status}"):
            details_tab, edit_tab = st.tabs(["Bilgiler", "Düzenle"])
            with details_tab:
                render_book_details(book)
            with edit_tab:
                render_book_editor(book)


def render_add_page():
    st.header("Yeni Kitap Ekle")

    camera_col, manual_col = st.columns([0.48, 0.52])
    with camera_col:
        img_file = st.camera_input(
            "Barkodu Okutun",
            key="barcode_camera",
            help="Streamlit kamera bileşeni arka kamerayı kesin zorlayamaz; mobil tarayıcı destekliyorsa arka kamerayı seçebilirsin.",
        )
        uploaded_barcode = st.file_uploader(
            "Barkod fotoğrafı yükle",
            type=["png", "jpg", "jpeg"],
            key="barcode_upload",
        )
    with manual_col:
        isbn_input = st.text_input("ISBN numarasını manuel girin", placeholder="9786052361917")
        manual_without_isbn = st.checkbox("ISBN olmadan manuel kitap ekle")

    target_isbn = ""
    decoded_camera = decode_isbn_from_image(img_file)
    decoded_upload = decode_isbn_from_image(uploaded_barcode)
    if decoded_camera:
        target_isbn = decoded_camera
        st.success(f"Kameradan barkod okundu: {target_isbn}")
    elif decoded_upload:
        target_isbn = decoded_upload
        st.success(f"Yüklenen görselden barkod okundu: {target_isbn}")
    elif img_file or uploaded_barcode:
        st.warning("Barkod okunamadı. Daha net bir görüntüyle tekrar deneyebilirsin.")

    if isbn_input:
        target_isbn = isbn_input.strip()

    lookup = None
    book_info = None
    if target_isbn:
        with st.spinner("Kitap aranıyor: Google Books, Open Library, ISBNdb ve Türkçe kaynaklar..."):
            lookup = get_book_info_comprehensive(target_isbn)
        normalized = lookup.get("normalized")
        book_info = lookup.get("book") or blank_book(target_isbn)

        if normalized:
            st.caption(f"Aranan ISBN varyasyonları: {', '.join(normalized['variants'])}")
            if not normalized.get("valid"):
                st.warning("ISBN yakalandı ama checksum geçersiz görünüyor. Yine de arama denendi.")

        if lookup["ok"] and book_info.get("title"):
            st.success(f"Kitap bulundu. Kaynaklar: {book_info.get('_sources', 'Bilinmiyor')}")
        else:
            st.warning(
                "Online kaynaklarda güvenilir kayıt bulunamadı. "
                "ISBN hazır; bilgileri elle girip kaydedebilirsin."
            )

    if manual_without_isbn and not book_info:
        book_info = blank_book("")

    if not book_info:
        return

    if book_info.get("cover_url"):
        st.image(book_info["cover_url"], width=140)

    with st.form("save_book_form"):
        st.subheader("Kitap Detayları")
        data = build_form_data("new_book", book_info)
        if target_isbn:
            normalized = normalize_isbn(target_isbn)
            data["isbn"] = normalized["isbn13"] if normalized else only_digits(target_isbn)
        submitted = st.form_submit_button("Kütüphaneye Kaydet", use_container_width=True)

    if submitted:
        if not clean_text(data.get("title")):
            st.error("Kitap adı zorunlu.")
            return

        existing = isbn_exists(data.get("isbn"))
        if existing:
            st.warning(f"Bu ISBN zaten kayıtlı: {existing.get('title', 'İsimsiz')}")
            return

        if insert_book(data):
            st.success(f"'{clean_text(data['title'])}' kütüphaneye eklendi.")
            set_page("library")
            st.rerun()


# --- UYGULAMA ---
inject_css()

if "page" not in st.session_state:
    st.session_state["page"] = "library"

all_books = fetch_books()
filters = render_sidebar(all_books)
render_top_bar()

if st.session_state.get("page") == "add":
    render_add_page()
else:
    render_library_page(all_books, filters)
