import base64
import csv
import html
import io
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
import streamlit as st
from bs4 import BeautifulSoup
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from pyzbar.pyzbar import decode
from supabase import Client, create_client

try:
    import cv2
    import numpy as np
except Exception:
    cv2 = None
    np = None

try:
    import isbnlib
except Exception:
    isbnlib = None


APP_DIR = Path(__file__).resolve().parent
APP_NAME = "Badgers' Kitap App"
BRAND_IMAGE_PATHS = [
    APP_DIR / "assets" / "badger.png",
    APP_DIR / "badger.png",
]

# --- KONFIGURASYON VE BAGLANTI ---
st.set_page_config(page_title=APP_NAME, page_icon="📚", layout="wide")

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}

REQUEST_TIMEOUT = 8
LOOKUP_CACHE_VERSION = 4
BULK_LOOKUP_TIMEOUT_SECONDS = 20

PAGE_LABELS = {
    "library": "🏠 Kütüphanem",
    "add": "🔍 Kitap Ekle",
    "bulk_add": "🧾 Toplu Kitap Ekle",
    "lookup_queue": "⏳ Sonra Aranacaklar",
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
    ("İkra Kitap", "https://www.ikrakitap.com/arama?q={isbn}"),
]

DIRECT_ISBN_PAGES = [
    ("Ucuzkitapal", "https://www.ucuzkitapal.com/{isbn}/"),
]

WEB_SEARCH_MAX_RESULTS = 8
WEB_SEARCH_TEMPLATES = [
    "https://duckduckgo.com/html/?q={query}",
    "https://www.bing.com/search?q={query}",
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


def is_noise_text(value: str) -> bool:
    value = clean_text(value)
    if not value:
        return True
    key = canonical_key(value)
    if not key:
        return True
    exact_noise = {
        "listesi",
        "populer",
        "popular",
        "cok satanlar",
        "cok satan",
        "en cok satanlar",
        "sinav kitaplari",
        "ders kitaplari",
        "okula yardimci",
        "kampanyalar",
        "tum kampanyalar",
        "urun",
        "yazar listesi",
        "yayinevi listesi",
        "kategori listesi",
        "tum kategoriler",
        "urunler",
        "menuler",
        "menu",
        "hesabim",
        "sepetim",
        "uye girisi",
        "giris yap",
        "kayit ol",
        "yorum yap",
        "alisveris listeme ekle",
        "favorilerime ekle",
        "sepete ekle",
        "devamini oku",
        "sonuc bulunamadi",
        "input",
        "button",
        "fork",
        "share",
    }
    if key in exact_noise:
        return True
    if len(value) > 180:
        return True
    if re.fullmatch(r"[%\d\s.,]+(tl|₺|eur|usd)?", key):
        return True
    noisy_starts = (
        "kitapyurdu fiyati",
        "uretici liste fiyati",
        "liste fiyati",
        "kazanciniz",
        "parapuan",
        "satis rakamlari",
        "stokta",
        "kargo",
    )
    return any(key.startswith(prefix) for prefix in noisy_starts)


def is_bad_title(title: str, variants: list[str] | None = None, source: str = "") -> bool:
    title = clean_text(title)
    if is_noise_text(title):
        return True
    key = canonical_key(title)
    if len(key) < 2:
        return True
    if not re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]", title):
        return True
    source_key = canonical_key(source)
    bad_phrases = (
        "arama",
        "search",
        "sonuc",
        "kitap bkmkitap bir kitapla mumkun",
        "kitapla bulusmanin en kolay yolu",
        "isbn search",
    )
    if any(phrase in key for phrase in bad_phrases):
        return True
    if variants and any(variant and variant in only_digits(title) for variant in variants):
        if source_key and source_key in key:
            return True
        if any(phrase in key for phrase in ("arama", "search", "isbn")) and len(key.split()) <= 8:
            return True
    return False


def clean_field_value(value: str) -> str:
    value = clean_text(value)
    return "" if is_noise_text(value) else value


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


def is_meaningful_book_record(record: dict, variants: list[str] | None = None) -> bool:
    variants = variants or [record.get("isbn", "")]
    if not record or is_bad_title(record.get("title"), variants, record.get("_source", "")):
        return False
    title_key = canonical_key(record.get("title"))
    if len(title_key.split()) == 1 and len(title_key) <= 2:
        return False

    author = clean_field_value(record.get("author"))
    publisher = clean_field_value(record.get("publisher"))
    page_count = clean_field_value(record.get("page_count"))
    source = clean_text(record.get("_source"))

    trusted_sources = ("Google Books", "Open Library", "ISBNdb", "isbnlib")
    if any(source.startswith(trusted) for trusted in trusted_sources):
        return True

    # Web kazıma kayıtları daha riskli olduğu için başlık dışında en az bir anlamlı alan isteriz.
    return bool(author or publisher or page_count)


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
def remaining_timeout(deadline: float | None, default_timeout: float = REQUEST_TIMEOUT) -> float | None:
    if deadline is None:
        return default_timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    return max(0.8, min(default_timeout, remaining))


def deadline_expired(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def http_get_json(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    deadline: float | None = None,
) -> dict | None:
    timeout = remaining_timeout(deadline)
    if timeout is None:
        return None
    try:
        response = requests.get(
            url,
            params=params,
            headers={**HTTP_HEADERS, **(headers or {})},
            timeout=timeout,
        )
        if response.status_code == 200:
            return response.json()
    except Exception:
        return None
    return None


def http_get_html(url: str, deadline: float | None = None) -> tuple[str, str]:
    timeout = remaining_timeout(deadline)
    if timeout is None:
        return "", ""
    try:
        response = requests.get(
            url,
            headers=HTTP_HEADERS,
            timeout=timeout,
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


def search_google_books(variants: list[str], deadline: float | None = None) -> list[dict]:
    records = []
    for isbn in variants:
        if deadline_expired(deadline):
            break
        data = http_get_json(
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": f"isbn:{isbn}", "maxResults": 5, "printType": "books"},
            deadline=deadline,
        )
        for item in (data or {}).get("items", []):
            record = google_record_from_item(item, variants[0], variants)
            if record:
                records.append(record)
    return records


# --- KAYNAK 2: OPEN LIBRARY ---
def openlibrary_author_name(author_key: str, deadline: float | None = None) -> str:
    if not author_key:
        return ""
    url = f"https://openlibrary.org{author_key}.json" if author_key.startswith("/") else author_key
    data = http_get_json(url, deadline=deadline)
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


def openlibrary_edition_record(edition: dict, isbn: str, deadline: float | None = None) -> dict | None:
    title = clean_text(edition.get("title"))
    if not title:
        return None

    author_names = []
    for author in edition.get("authors", [])[:3]:
        if deadline_expired(deadline):
            break
        name = openlibrary_author_name(author.get("key", ""), deadline=deadline)
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


def search_openlibrary(variants: list[str], deadline: float | None = None) -> list[dict]:
    records = []
    for isbn in variants:
        if deadline_expired(deadline):
            break
        data = http_get_json(
            "https://openlibrary.org/api/books",
            params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
            deadline=deadline,
        )
        entry = (data or {}).get(f"ISBN:{isbn}")
        if entry:
            record = openlibrary_data_record(entry, variants[0])
            if record:
                records.append(record)

        if deadline_expired(deadline):
            break
        edition = http_get_json(f"https://openlibrary.org/isbn/{isbn}.json", deadline=deadline)
        if edition:
            record = openlibrary_edition_record(edition, variants[0], deadline=deadline)
            if record:
                records.append(record)

        if deadline_expired(deadline):
            break
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
            deadline=deadline,
        )
        for doc in (search or {}).get("docs", []):
            record = openlibrary_search_record(doc, variants[0], variants)
            if record:
                records.append(record)
    return records


# --- OPSIYONEL KAYNAK 3: ISBNDB ---
def search_isbndb(variants: list[str], deadline: float | None = None) -> list[dict]:
    api_key = get_secret("ISBNDB_API_KEY")
    if not api_key:
        return []

    records = []
    headers = {"Authorization": api_key, "x-api-key": api_key}
    for isbn in variants:
        if deadline_expired(deadline):
            break
        for base_url in ("https://api2.isbndb.com/book/", "https://api.isbndb.com/book/"):
            if deadline_expired(deadline):
                break
            data = http_get_json(base_url + isbn, headers=headers, deadline=deadline)
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


def search_isbnlib(variants: list[str], deadline: float | None = None) -> list[dict]:
    """isbnlib ISBN doğrulama/metadata için ek kaynaktır; barkod görseli okumaz."""
    if isbnlib is None:
        return []

    records = []
    services = ("goob", "openl", "wiki")
    for isbn in variants:
        if deadline_expired(deadline):
            break
        try:
            canonical = isbnlib.canonical(isbn)
        except Exception:
            canonical = isbn
        if not canonical:
            continue

        for service in services:
            if deadline_expired(deadline):
                break
            try:
                data = isbnlib.meta(canonical, service=service)
            except Exception:
                data = {}
            if not data:
                continue

            isbn13 = clean_text(data.get("ISBN-13") or data.get("ISBN13") or canonical)
            if isbn13 and isbn13 not in variants and only_digits(isbn13) not in variants:
                continue

            title = clean_text(data.get("Title") or data.get("title"))
            if is_bad_title(title, variants, "isbnlib"):
                continue

            authors = data.get("Authors") or data.get("authors") or ""
            if isinstance(authors, list):
                authors = ", ".join(clean_text(author) for author in authors)

            book = blank_book(variants[0])
            book.update(
                {
                    "title": title,
                    "author": dedupe_comma_values(authors),
                    "publisher": normalize_publisher_name(data.get("Publisher") or data.get("publisher")),
                    "published_date": clean_text(data.get("Year") or data.get("year")),
                    "first_print_year": first_year(data.get("Year") or data.get("year")),
                    "language": pretty_language(data.get("Language") or data.get("language")),
                    "source_url": "",
                    "_source": f"isbnlib/{service}",
                    "_isbn_matched": True,
                }
            )
            if is_meaningful_book_record(book, variants):
                records.append(book)
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
    isbn_matched = variant_in_text(variants, serialized)
    if has_isbn_field and not isbn_matched:
        return None

    title = clean_text(obj.get("name") or obj.get("headline"))
    if not title or is_bad_title(title, variants, source):
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
            "_isbn_matched": isbn_matched,
        }
    )
    return sanitize_scraped_record(book, variants, source)


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
        "isbn",
    }
    for index, line in enumerate(lines):
        folded = line.casefold()
        for label, folded_label in zip(labels, label_set):
            if folded.startswith(folded_label):
                value = clean_text(line[len(label):].strip(" :;-"))
                if (
                    value
                    and value.casefold() not in blocked_next_words
                    and not is_noise_text(value)
                    and len(value) < 150
                ):
                    return value
                if index + 1 < len(lines):
                    next_line = clean_text(lines[index + 1])
                    if (
                        next_line
                        and next_line.casefold() not in blocked_next_words
                        and not is_noise_text(next_line)
                        and len(next_line) < 150
                    ):
                        return next_line
    return ""


def looks_like_search_page(url: str, title: str = "") -> bool:
    parsed = urlparse(url)
    path_query = canonical_key(f"{parsed.path} {parsed.query} {title}")
    search_tokens = (
        "arama",
        "search",
        "route product search",
        "filter name",
        "q ",
        "query",
        "sonuc",
        "category",
        "kategori",
    )
    return any(token in path_query for token in search_tokens)


def looks_like_publisher(value: str) -> bool:
    key = canonical_key(value)
    return any(
        token in key
        for token in (
            "yayinevi",
            "yayinlari",
            "yayincilik",
            "kitap",
            "press",
            "publishing",
            "nesriyat",
            "timas",
            "say",
        )
    )


def context_lines_after_title(lines: list[str], title: str) -> list[str]:
    title_key = canonical_key(title)
    if not title_key:
        return []
    for index, line in enumerate(lines):
        line_key = canonical_key(line)
        if line_key == title_key or title_key in line_key or line_key in title_key:
            result = []
            for next_line in lines[index + 1 : index + 10]:
                if is_noise_text(next_line):
                    continue
                if variant_in_text([], next_line):
                    continue
                result.append(next_line)
            return result
    return []


def clean_context_lines(lines: list[str]) -> list[str]:
    cleaned = []
    for line in lines:
        line = clean_text(line)
        if not line or is_noise_text(line):
            continue
        if canonical_key(line) in {"isbn", "barkod", "ean"}:
            continue
        cleaned.append(line)
    return unique_keep_order(cleaned)


def enrich_from_title_context(book: dict, lines: list[str], source: str) -> dict:
    title = clean_text(book.get("title"))
    following = clean_context_lines(context_lines_after_title(lines, title))
    if not following:
        return book

    source_key = canonical_key(source)
    author = clean_field_value(book.get("author"))
    publisher = clean_field_value(book.get("publisher"))

    if "bkm" in source_key:
        if not publisher and len(following) >= 1:
            publisher = following[0]
        if not author and len(following) >= 2:
            author = following[1]
    elif "ikra" in source_key:
        if not author and len(following) >= 1:
            author = following[0]
        if not publisher and len(following) >= 2:
            publisher = following[1]
    else:
        if not publisher:
            publisher = next((line for line in following[:4] if looks_like_publisher(line)), "")
        if not author:
            if publisher and publisher in following:
                pub_index = following.index(publisher)
                candidates = following[:pub_index] + following[pub_index + 1 :]
            else:
                candidates = following
            author = next(
                (
                    line
                    for line in candidates[:4]
                    if not looks_like_publisher(line) and not variant_in_text([book.get("isbn", "")], line)
                ),
                "",
            )

    if author:
        book["author"] = dedupe_comma_values(author)
    if publisher:
        book["publisher"] = normalize_publisher_name(publisher)
    return book


def parse_isbn_detail_line(line: str, book: dict, variants: list[str]) -> dict:
    if not variant_in_text(variants, line):
        return book
    parts = [clean_text(part) for part in line.split("|")]
    parts = [part for part in parts if part]
    for part in parts:
        if variant_in_text(variants, part):
            continue
        if re.fullmatch(r"\d{2,4}", only_digits(part)) and not book.get("page_count"):
            book["page_count"] = only_digits(part)
        elif canonical_key(part) in {"turkce", "tr", "turkish"} and not book.get("language"):
            book["language"] = "Türkçe"
        elif "kağıt" in part.casefold() or "kagit" in canonical_key(part):
            book["paper_type"] = part
        elif not book.get("paper_type") and "hamur" in canonical_key(part):
            book["paper_type"] = part
    return book


def record_from_isbn_context(lines: list[str], variants: list[str], source: str, url: str, title_hint: str = "") -> dict | None:
    for index, line in enumerate(lines):
        if not variant_in_text(variants, line):
            continue

        before = clean_context_lines(lines[max(0, index - 10) : index])
        after = clean_context_lines(lines[index + 1 : index + 10])
        window = before + [line] + after

        author = line_value(window, ["Yazar", "Yazarlar", "Author"])
        publisher = normalize_publisher_name(
            line_value(window, ["Yayınevi", "Yayıncı", "Yayinevi", "Publisher"])
        )
        translator = line_value(window, ["Çevirmen", "Cevirmen", "Translator"])

        title = "" if is_bad_title(title_hint, variants, source) else clean_text(title_hint)
        if not title:
            if publisher and publisher in before:
                pub_index = before.index(publisher)
                title_candidates = before[:pub_index]
            else:
                title_candidates = before
            for candidate in reversed(title_candidates):
                if (
                    not is_bad_title(candidate, variants, source)
                    and not looks_like_publisher(candidate)
                    and not canonical_key(candidate).startswith("yazar")
                    and candidate != author
                ):
                    title = candidate
                    break

        if not title:
            continue

        if not publisher:
            publisher = next((candidate for candidate in before if looks_like_publisher(candidate)), "")
        if not author:
            candidates = [candidate for candidate in before if candidate not in {title, publisher}]
            author = next((candidate for candidate in reversed(candidates) if not looks_like_publisher(candidate)), "")

        book = blank_book(variants[0])
        book.update(
            {
                "title": title,
                "author": dedupe_comma_values(author),
                "translator": dedupe_comma_values(translator),
                "publisher": normalize_publisher_name(publisher),
                "page_count": line_value(window, ["Sayfa Sayısı", "Sayfa", "Pages"]),
                "paper_type": line_value(window, ["Hamur Tipi", "Kağıt", "Kağıt Cinsi", "Kagit Cinsi"]),
                "dimensions": line_value(window, ["Ebat", "Boyut", "Kitap Boyutu", "Boyutlar"]),
                "first_print_year": first_year(
                    line_value(window, ["İlk Baskı Yılı", "Basım Tarihi", "Basım Yılı", "Yayın Tarihi"])
                ),
                "print_edition": line_value(window, ["Baskı", "Baskı Sayısı", "Baski Sayisi"]),
                "language": pretty_language(line_value(window, ["Dil", "Yayın Dili", "Kitap Dili", "Basım Dili"])),
                "source_url": url,
                "_source": f"{source} ISBN bağlamı",
                "_isbn_matched": True,
            }
        )
        book = parse_isbn_detail_line(line, book, variants)
        return sanitize_scraped_record(book, variants, source)
    return None


def sanitize_scraped_record(record: dict, variants: list[str], source: str) -> dict | None:
    if not record:
        return None
    if is_bad_title(record.get("title"), variants, source):
        return None
    for field in ("author", "translator", "publisher"):
        record[field] = clean_field_value(record.get(field))
    record["author"] = dedupe_comma_values(record.get("author"))
    record["translator"] = dedupe_comma_values(record.get("translator"))
    record["publisher"] = normalize_publisher_name(record.get("publisher"))
    if not is_meaningful_book_record(record, variants):
        return None
    return record


def extract_book_from_html(html: str, variants: list[str], source: str, url: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ")
    page_has_isbn = variant_in_text(variants, page_text)
    lines = [clean_text(line) for line in soup.get_text("\n").splitlines()]
    lines = [line for line in lines if line]

    h1_title = clean_text(soup.find("h1").get_text(" ") if soup.find("h1") else "")
    meta_title = clean_text(meta_content(soup, "og:title", "twitter:title"))
    document_title = clean_text(soup.title.get_text(" ") if soup.title else "")
    title_hint = h1_title or meta_title or document_title
    search_page = looks_like_search_page(url, title_hint)

    records = []

    for obj in jsonld_objects(soup):
        record = record_from_jsonld(obj, variants, source, url)
        if record and (page_has_isbn or record.get("_isbn_matched")):
            records.append(record)

    context_record = record_from_isbn_context(lines, variants, source, url, title_hint=title_hint)
    if context_record:
        records.append(context_record)

    if records and (page_has_isbn or any(record.get("_isbn_matched") for record in records)):
        return sanitize_scraped_record(max(records, key=score_record), variants, source)
    if not page_has_isbn:
        return None
    if search_page:
        return None

    title = (
        h1_title
        or meta_title
        or document_title
    )
    for separator in (" | ", " - "):
        if separator in title and source.casefold() in title.casefold():
            title = clean_text(title.split(separator)[0])

    if is_bad_title(title, variants, source):
        return None

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
            "paper_type": line_value(lines, ["Hamur Tipi", "Kağıt", "Kağıt Cinsi", "Kagit Cinsi"]),
            "dimensions": line_value(lines, ["Ebat", "Boyut", "Kitap Boyutu", "Boyutlar"]),
            "first_print_year": first_year(
                line_value(lines, ["İlk Baskı Yılı", "Basım Tarihi", "Basım Yılı", "Yayın Tarihi"])
            ),
            "print_edition": line_value(lines, ["Baskı", "Baskı Sayısı", "Baski Sayisi"]),
            "language": pretty_language(line_value(lines, ["Dil", "Yayın Dili", "Kitap Dili", "Basım Dili"])),
            "cover_url": meta_content(soup, "og:image", "twitter:image"),
            "source_url": url,
            "_source": source,
            "_isbn_matched": True,
        }
    )
    book = enrich_from_title_context(book, lines, source)
    return sanitize_scraped_record(book, variants, source)


def candidate_product_links(soup: BeautifulSoup, base_url: str, variants: list[str]) -> list[str]:
    candidates = []
    bad_tokens = (
        "sepet",
        "cart",
        "login",
        "uye",
        "üyelik",
        "arama",
        "search",
        "javascript:",
        "mailto:",
        "kategori",
        "category",
        "yazar",
        "yayinevi",
        "marka",
        "kampanya",
        "cok-satan",
        "çok-satan",
    )
    good_tokens = ("kitap", "urun", "ürün", "product", "/p/", "p-", ".html")

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        full_url = urljoin(base_url, href)
        folded = full_url.casefold()
        if any(token in folded for token in bad_tokens):
            continue
        link_text = clean_text(anchor.get_text(" "))
        if is_noise_text(link_text):
            continue
        score = 0
        if any(variant and variant in only_digits(full_url + " " + link_text) for variant in variants):
            score += 10
        if any(token in folded for token in good_tokens):
            score += 4
        path_parts = [part for part in urlparse(full_url).path.split("/") if part]
        if len(path_parts) == 1 and len(path_parts[0]) > 3:
            score += 2
        if len(link_text) > 3:
            score += 1
        if score > 0:
            candidates.append((score, full_url))

    ordered = []
    for _, link in sorted(candidates, key=lambda item: item[0], reverse=True):
        if link not in ordered:
            ordered.append(link)
        if len(ordered) >= 24:
            break
    return ordered


def search_turkish_retailers(
    variants: list[str],
    deadline: float | None = None,
    link_limit: int = 10,
) -> list[dict]:
    records = []
    for source, template in TURKISH_RETAILERS:
        if deadline_expired(deadline):
            break
        source_found = False
        for isbn in variants:
            if deadline_expired(deadline):
                break
            search_url = template.format(isbn=isbn)
            final_url, html = http_get_html(search_url, deadline=deadline)
            if not html:
                continue

            record = extract_book_from_html(html, variants, source, final_url)
            if record:
                records.append(record)
                source_found = True
                break

            soup = BeautifulSoup(html, "html.parser")
            for link in candidate_product_links(soup, final_url, variants)[:link_limit]:
                if deadline_expired(deadline):
                    break
                product_url, product_html = http_get_html(link, deadline=deadline)
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


def search_direct_isbn_pages(variants: list[str], deadline: float | None = None) -> list[dict]:
    records = []
    for source, template in DIRECT_ISBN_PAGES:
        if deadline_expired(deadline):
            break
        for isbn in variants:
            if deadline_expired(deadline):
                break
            final_url, html = http_get_html(template.format(isbn=isbn), deadline=deadline)
            if not html:
                continue
            record = extract_book_from_html(html, variants, source, final_url)
            if record:
                records.append(record)
                break
    return records


def unwrap_search_result_url(href: str, base_url: str) -> str:
    full_url = urljoin(base_url, href)
    parsed = urlparse(full_url)
    query = parse_qs(parsed.query)
    for key in ("uddg", "url", "u"):
        if query.get(key):
            return unquote(query[key][0])
    return full_url


def source_name_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower().replace("www.", "")
    if not host:
        return "Web"
    return host.split(":")[0]


def should_skip_web_result(url: str) -> bool:
    parsed = urlparse(url)
    host_path = canonical_key(f"{parsed.netloc} {parsed.path}")
    blocked = (
        "google",
        "duckduckgo",
        "youtube",
        "facebook",
        "instagram",
        "twitter",
        "x com",
        "linkedin",
        "pinterest",
        "sikayetvar",
        "pdf",
        "login",
        "cart",
        "sepet",
    )
    return not parsed.scheme.startswith("http") or any(token in host_path for token in blocked)


def search_web_for_isbn_pages(
    variants: list[str],
    deadline: float | None = None,
    max_results: int = WEB_SEARCH_MAX_RESULTS,
) -> list[dict]:
    """Son şans: ISBN'i web arama sonucu olarak bulup sayfaları tek tek doğrular."""
    records = []
    seen_links = set()
    primary_isbn = variants[0]
    query = quote_plus(f'"{primary_isbn}" kitap ISBN')
    search_pages = []
    for template in WEB_SEARCH_TEMPLATES:
        if deadline_expired(deadline):
            break
        final_url, html = http_get_html(template.format(query=query), deadline=deadline)
        if html:
            search_pages.append((final_url, html))

    links = []
    for final_url, html in search_pages:
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.find_all("a", href=True):
            link = unwrap_search_result_url(anchor["href"], final_url)
            if should_skip_web_result(link) or link in seen_links:
                continue
            seen_links.add(link)
            links.append(link)
            if len(links) >= max_results:
                break
        if len(links) >= max_results:
            break

    for link in links:
        if deadline_expired(deadline):
            break
        page_url, page_html = http_get_html(link, deadline=deadline)
        if not page_html:
            continue
        source = source_name_from_url(page_url)
        record = extract_book_from_html(page_html, variants, source, page_url)
        if record:
            records.append(record)
    return records


# --- KAYITLARI BIRLESTIRME ---
def score_record(record: dict) -> int:
    if is_bad_title(record.get("title"), [record.get("isbn", "")], record.get("_source", "")):
        return 0
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
    usable = [
        record
        for record in records
        if is_meaningful_book_record(record, normalized.get("variants", []))
        and score_record(record) > 0
    ]
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
def get_book_info_comprehensive(
    raw_isbn: str,
    cache_version: int = LOOKUP_CACHE_VERSION,
    max_seconds: int | None = None,
    web_result_limit: int = WEB_SEARCH_MAX_RESULTS,
    retailer_link_limit: int = 10,
) -> dict:
    _ = cache_version
    deadline = time.monotonic() + max_seconds if max_seconds else None
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
    records.extend(search_google_books(variants, deadline=deadline))
    if not deadline_expired(deadline):
        records.extend(search_openlibrary(variants, deadline=deadline))
    if not deadline_expired(deadline) and max_seconds is None:
        records.extend(search_isbnlib(variants, deadline=deadline))
    if not deadline_expired(deadline):
        records.extend(search_isbndb(variants, deadline=deadline))
    if not deadline_expired(deadline):
        records.extend(search_direct_isbn_pages(variants, deadline=deadline))

    merged = merge_records(normalized, records)
    if (
        not deadline_expired(deadline)
        and (not merged or merged["_score"] < 18 or not merged.get("publisher") or not merged.get("page_count"))
    ):
        records.extend(search_turkish_retailers(variants, deadline=deadline, link_limit=retailer_link_limit))
        merged = merge_records(normalized, records)

    if not deadline_expired(deadline) and (not merged or merged["_score"] < 18 or not merged.get("title")):
        records.extend(search_web_for_isbn_pages(variants, deadline=deadline, max_results=web_result_limit))
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


def fetch_library_summary() -> dict:
    summary = {"count": 0, "recent": []}
    try:
        count_response = supabase.table("books").select("id", count="exact").limit(1).execute()
        summary["count"] = count_response.count or 0
    except Exception:
        summary["count"] = 0

    try:
        recent_response = (
            supabase.table("books")
            .select("title,author,created_at")
            .order("created_at", desc=True)
            .limit(2)
            .execute()
        )
        summary["recent"] = recent_response.data or []
    except Exception:
        summary["recent"] = []
    return summary


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


def normalize_lookup_isbn(raw_isbn: str) -> str:
    normalized = normalize_isbn(raw_isbn)
    if normalized:
        return normalized.get("isbn13") or normalized["variants"][0]
    return only_digits(raw_isbn)


def google_search_url_for_isbn(isbn: str) -> str:
    isbn = normalize_lookup_isbn(isbn)
    return f"https://www.google.com/search?q={quote_plus(f'{isbn} kitap ISBN')}"


def save_pending_isbn(raw_isbn: str, note: str = "", source: str = "manual") -> bool:
    isbn = normalize_lookup_isbn(raw_isbn)
    if not isbn:
        st.error("Kaydedilecek geçerli bir ISBN bulunamadı.")
        return False
    payload = {
        "isbn": isbn,
        "status": "bekliyor",
        "source": clean_text(source),
        "note": clean_text(note),
    }
    try:
        supabase.table("isbn_lookup_queue").upsert(payload, on_conflict="isbn").execute()
        return True
    except Exception as exc:
        st.error(
            "ISBN'i sonra arama listesine kaydedemedim. "
            "Supabase'te yeni kuyruk tablosu için migration SQL'ini çalıştırman gerekiyor."
        )
        st.caption(str(exc))
        return False


def fetch_pending_isbns() -> list[dict]:
    try:
        response = (
            supabase.table("isbn_lookup_queue")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        return response.data or []
    except Exception:
        return []


def update_pending_isbn_status(queue_id, status: str) -> bool:
    try:
        supabase.table("isbn_lookup_queue").update({"status": status}).eq("id", queue_id).execute()
        return True
    except Exception as exc:
        st.error(f"Kuyruk durumu güncellenemedi: {exc}")
        return False


def delete_pending_isbn(queue_id) -> bool:
    try:
        supabase.table("isbn_lookup_queue").delete().eq("id", queue_id).execute()
        return True
    except Exception as exc:
        st.error(f"Kuyruk kaydı silinemedi: {exc}")
        return False


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
        "app": APP_NAME,
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
        .brand-wrap {
            display: flex;
            align-items: center;
            gap: 1.25rem;
            margin: 0.15rem 0 2rem 0;
        }
        .brand-logo-box {
            width: 210px;
            height: 210px;
            padding: 14px;
            border-radius: 14px;
            background: #d8cbb8;
            overflow: visible;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .brand-logo-box img {
            width: 100%;
            height: 100%;
            object-fit: contain;
            object-position: center center;
            display: block;
            border-radius: 8px;
        }
        .brand-title {
            margin: 0;
            font-size: 2rem;
            line-height: 1.1;
            font-weight: 750;
        }
        @media (max-width: 700px) {
            .brand-wrap {
                align-items: flex-start;
                flex-direction: column;
                gap: 0.8rem;
            }
            .brand-logo-box {
                width: 170px;
                height: 170px;
            }
        }
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


def local_image_data_uri(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/{mime};base64,{encoded}"


def render_top_bar():
    left, right = st.columns([0.82, 0.18], vertical_alignment="center")
    with left:
        brand_image = next((path for path in BRAND_IMAGE_PATHS if path.exists()), None)
        if brand_image:
            st.markdown(
                f"""
                <div class="brand-wrap">
                    <div class="brand-logo-box">
                        <img src="{local_image_data_uri(brand_image)}" alt="{html.escape(APP_NAME)} logosu">
                    </div>
                    <h1 class="brand-title">{html.escape(APP_NAME)}</h1>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.title(APP_NAME)
    with right:
        st.write("")
        if st.button("➕ Kitap Ekle", use_container_width=True, key="top_add_book"):
            set_page("add")
            st.rerun()


def render_sidebar(all_books: list[dict] | None = None):
    st.sidebar.title(f"📚 {APP_NAME}")

    current = st.session_state.get("page", "add")
    labels = list(PAGE_LABELS.values())
    reverse_labels = {value: key for key, value in PAGE_LABELS.items()}
    selected_label = st.sidebar.radio(
        "Menü",
        labels,
        index=labels.index(PAGE_LABELS.get(current, PAGE_LABELS["add"])),
    )
    selected_page = reverse_labels[selected_label]
    st.session_state["page"] = selected_page

    if selected_page != "library":
        return "Tümü", "Tümü", "Tüm Etiketler", "", "Eser Adı (A-Z)", []

    if all_books is None:
        all_books = fetch_books()

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
    backup_name = f"badgers_kitap_app_yedek_{datetime.now().strftime('%Y%m%d_%H%M')}"
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
    if st.sidebar.button("Kitap arama önbelleğini temizle", use_container_width=True):
        st.cache_data.clear()
        st.sidebar.success("Arama önbelleği temizlendi.")

    return personal_filter, status_filter, tag_filter, search_term, sort_by, all_books


def render_cover(cover_url: str, width: int = 120):
    cover_url = clean_text(cover_url)
    if cover_url:
        st.image(cover_url, width=width)
    else:
        st.caption("Kapak yok")


def isbn_from_barcode_payload(payload: str) -> str:
    payload = clean_text(payload)
    normalized = normalize_isbn(payload)
    if normalized:
        return normalized["isbn13"] or normalized["variants"][0]

    digits = only_digits(payload)
    # Bazı görüntülerde EAN-13'ün soldaki ilk hanesi ayrı okunabilir. 12 hane geldiyse
    # başına 9 ekleyip geçerli ISBN-13 oluyor mu diye güvenli şekilde deneriz.
    if len(digits) == 12:
        maybe_isbn = "9" + digits
        normalized = normalize_isbn(maybe_isbn)
        if normalized and normalized["valid"]:
            return normalized["isbn13"]
    return ""


def barcode_payloads_from_pil(image: Image.Image) -> list[str]:
    payloads = []
    try:
        for item in decode(image):
            value = item.data.decode("utf-8", errors="ignore").strip()
            if value:
                payloads.append(value)
    except Exception:
        pass
    return payloads


def pil_barcode_variants(image: Image.Image) -> list[Image.Image]:
    image = ImageOps.exif_transpose(image).convert("RGB")
    width, height = image.size
    crops = [
        image,
        image.crop((0, int(height * 0.25), width, height)),
        image.crop((0, int(height * 0.35), width, height)),
        image.crop((0, int(height * 0.45), width, height)),
        image.crop((int(width * 0.05), int(height * 0.25), int(width * 0.95), height)),
    ]

    variants = []
    for crop in crops:
        gray = ImageOps.grayscale(crop)
        base_variants = [
            crop,
            gray,
            ImageOps.autocontrast(gray),
            ImageEnhance.Contrast(gray).enhance(2.2),
            ImageEnhance.Sharpness(ImageOps.autocontrast(gray)).enhance(2.0),
        ]
        for item in base_variants:
            for scale in (1, 2, 3):
                resized = item if scale == 1 else item.resize((item.width * scale, item.height * scale))
                variants.append(resized)
                variants.append(resized.filter(ImageFilter.SHARPEN))
                if resized.mode != "1":
                    thresholded = ImageOps.grayscale(resized).point(lambda px: 255 if px > 145 else 0, mode="1")
                    variants.append(thresholded)

        for angle in (-6, -3, 3, 6):
            rotated = crop.rotate(angle, expand=True, fillcolor="white")
            variants.append(rotated)
            variants.append(ImageOps.autocontrast(ImageOps.grayscale(rotated)))

    # Çok büyük listeyi sınırlayıp aynı boyut/mod tekrarlarını azaltır.
    unique = []
    seen = set()
    for variant in variants:
        key = (variant.size, variant.mode)
        if key in seen:
            continue
        seen.add(key)
        unique.append(variant)
    return unique[:90]


def cv2_barcode_variants(image: Image.Image) -> list[Image.Image]:
    if cv2 is None or np is None:
        return []

    variants = []
    try:
        rgb = np.array(ImageOps.exif_transpose(image).convert("RGB"))
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        gray = cv2.resize(gray, None, fx=2.2, fy=2.2, interpolation=cv2.INTER_CUBIC)
        gray = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)

        processed_arrays = [
            gray,
            clahe,
            cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
            cv2.adaptiveThreshold(
                clahe,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                31,
                7,
            ),
        ]

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        processed_arrays.append(cv2.morphologyEx(processed_arrays[-1], cv2.MORPH_CLOSE, kernel))

        for array in processed_arrays:
            variants.append(Image.fromarray(array))
    except Exception:
        return []
    return variants


def decode_isbn_from_image(image_file) -> str:
    if not image_file:
        return ""
    try:
        if hasattr(image_file, "seek"):
            image_file.seek(0)
        image = Image.open(image_file)
    except Exception:
        return ""

    for variant in [image, *pil_barcode_variants(image), *cv2_barcode_variants(image)]:
        for payload in barcode_payloads_from_pil(variant):
            isbn = isbn_from_barcode_payload(payload)
            if isbn:
                return isbn
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


def blank_bulk_rows(count: int = 25) -> list[dict]:
    return [
        {
            "isbn": "",
            "title": "",
            "author": "",
            "publisher": "",
            "first_print_year": "",
            "page_count": "",
            "reading_status": "Okunacak",
            "category": "Kategorisiz",
            "tags": "",
            "notes": "",
        }
        for _ in range(count)
    ]


def editor_rows_to_list(editor_value) -> list[dict]:
    if editor_value is None:
        return []
    if hasattr(editor_value, "to_dict"):
        return editor_value.to_dict("records")
    return list(editor_value)


def row_has_any_book_data(row: dict) -> bool:
    return any(clean_text(row.get(key)) for key in ("isbn", "title", "author", "publisher", "notes", "tags"))


def bulk_row_to_book_data(row: dict, auto_lookup: bool) -> tuple[dict | None, bool]:
    row = {key: clean_text(value) for key, value in row.items()}
    isbn = normalize_lookup_isbn(row.get("isbn"))
    lookup_used = False
    data = blank_book(isbn)

    if auto_lookup and isbn:
        lookup = get_book_info_comprehensive(
            isbn,
            max_seconds=BULK_LOOKUP_TIMEOUT_SECONDS,
            web_result_limit=4,
            retailer_link_limit=4,
        )
        if lookup.get("ok") and lookup.get("book"):
            data.update(lookup["book"])
            lookup_used = True

    manual_values = {
        "isbn": isbn,
        "title": row.get("title"),
        "author": row.get("author"),
        "publisher": row.get("publisher"),
        "first_print_year": row.get("first_print_year"),
        "page_count": row.get("page_count"),
        "reading_status": row.get("reading_status") or "Okunacak",
        "category": row.get("category") or "Kategorisiz",
        "tags": parse_tags(row.get("tags")),
        "notes": row.get("notes"),
    }

    for key, value in manual_values.items():
        if value not in ("", [], None):
            data[key] = value

    if not clean_text(data.get("title")):
        return None, lookup_used
    return data, lookup_used


def process_bulk_barcode_images(image_files: list, save_unresolved: bool = True) -> dict:
    result = {
        "decoded": [],
        "inserted": [],
        "unresolved": [],
        "duplicates": [],
        "unreadable": [],
        "errors": [],
    }
    seen_isbns = set()

    for index, image_file in enumerate(image_files[:10], start=1):
        label = getattr(image_file, "name", "") or f"Barkod {index}"
        isbn = decode_isbn_from_image(image_file)
        if not isbn:
            result["unreadable"].append(label)
            continue

        isbn = normalize_lookup_isbn(isbn)
        if isbn in seen_isbns:
            result["duplicates"].append(isbn)
            continue
        seen_isbns.add(isbn)
        result["decoded"].append(isbn)

        if isbn_exists(isbn):
            result["duplicates"].append(isbn)
            continue

        lookup = get_book_info_comprehensive(
            isbn,
            cache_version=LOOKUP_CACHE_VERSION + 1,
            max_seconds=BULK_LOOKUP_TIMEOUT_SECONDS,
            web_result_limit=4,
            retailer_link_limit=4,
        )
        if lookup.get("ok") and lookup.get("book", {}).get("title"):
            book_data = lookup["book"]
            book_data["isbn"] = isbn
            if insert_book(book_data):
                result["inserted"].append(
                    {
                        "isbn": isbn,
                        "title": clean_text(book_data.get("title")),
                    }
                )
            else:
                result["errors"].append(isbn)
        else:
            result["unresolved"].append(isbn)
            if save_unresolved:
                save_pending_isbn(isbn, note="Toplu barkod okutma sırasında bulunamadı", source="bulk_barcode")

    return result


def render_bulk_barcode_section():
    with st.expander("Toplu Barkod Okutma", expanded=True):
        st.caption("En fazla 10 barkod fotoğrafını tek seferde işleyebilirsin.")
        uploaded_files = st.file_uploader(
            "Barkod fotoğraflarını yükle",
            type=["png", "jpg", "jpeg"],
            accept_multiple_files=True,
            key="bulk_barcode_uploads",
        )
        show_camera_slots = st.checkbox("Kamera ile 10 barkod kutusu göster", value=False)

        camera_files = []
        if show_camera_slots:
            for row_index in range(5):
                col1, col2 = st.columns(2)
                with col1:
                    captured = st.camera_input(
                        f"Barkod {row_index * 2 + 1}",
                        key=f"bulk_barcode_camera_{row_index * 2 + 1}",
                    )
                    if captured:
                        camera_files.append(captured)
                with col2:
                    captured = st.camera_input(
                        f"Barkod {row_index * 2 + 2}",
                        key=f"bulk_barcode_camera_{row_index * 2 + 2}",
                    )
                    if captured:
                        camera_files.append(captured)

        save_unresolved_barcodes = st.checkbox(
            "Bulunamayan barkodları Sonra Aranacaklar listesine kaydet",
            value=True,
            key="bulk_barcode_save_unresolved",
        )
        image_files = list(uploaded_files or []) + camera_files
        if len(image_files) > 10:
            st.warning("İlk 10 barkod işlenecek.")

        if st.button("Barkodları Oku, Ara ve Ekle", type="primary", use_container_width=True):
            if not image_files:
                st.warning("Önce barkod fotoğrafı ekle.")
                return

            with st.spinner("Barkodlar okunuyor, kitap bilgileri aranıyor ve kayıt yapılıyor..."):
                result = process_bulk_barcode_images(image_files, save_unresolved=save_unresolved_barcodes)
            st.session_state["last_bulk_barcode_result"] = result

        result = st.session_state.get("last_bulk_barcode_result")
        if not result:
            return

        st.success(f"{len(result['inserted'])} kitap eklendi.")
        if result["inserted"]:
            st.write("**Eklenenler:**")
            for item in result["inserted"]:
                st.write(f"- {item['isbn']} · {item['title']}")
        if result["unresolved"]:
            st.warning("Bulunamayan ISBN'ler:")
            for isbn in result["unresolved"]:
                st.markdown(f"- `{isbn}` · [Google'da ara]({google_search_url_for_isbn(isbn)})")
        if result["unreadable"]:
            st.warning("Barkodu okunamayan görseller:")
            for name in result["unreadable"]:
                st.write(f"- {name}")
        if result["duplicates"]:
            st.info("Tekrar veya zaten kayıtlı olan ISBN'ler:")
            for isbn in unique_keep_order(result["duplicates"]):
                st.write(f"- {isbn}")
        if result["errors"]:
            st.error("Kayıt sırasında hata alınan ISBN'ler:")
            for isbn in result["errors"]:
                st.write(f"- {isbn}")


def parse_isbns_from_text(value: str) -> list[str]:
    candidates = []
    for chunk in re.split(r"[\s,;]+", str(value or "")):
        normalized = normalize_isbn(chunk)
        if normalized:
            candidates.append(normalized["isbn13"] or normalized["variants"][0])
    digits = only_digits(value)
    candidates.extend(match.group(0) for match in re.finditer(r"97[89]\d{10}", digits))
    return unique_keep_order([normalize_lookup_isbn(candidate) for candidate in candidates])


def ensure_ultimate_barcode_state():
    st.session_state.setdefault("ultimate_barcodes", [])
    st.session_state.setdefault("ultimate_camera_nonce", 0)
    st.session_state.setdefault("ultimate_lookup_result", None)


def add_to_ultimate_barcodes(isbns: list[str]):
    current = st.session_state.get("ultimate_barcodes", [])
    st.session_state["ultimate_barcodes"] = unique_keep_order([*current, *isbns])


def current_ultimate_barcodes_from_editor(editor_value) -> list[str]:
    rows = editor_rows_to_list(editor_value)
    isbns = []
    for row in rows:
        isbn = normalize_lookup_isbn(row.get("isbn"))
        if isbn:
            isbns.append(isbn)
    return unique_keep_order(isbns)


def render_ultimate_barcode_section():
    ensure_ultimate_barcode_state()
    with st.expander("Ultimate Barkod Listesi", expanded=False):
        st.caption(
            "Barkodları önce listeye topla. Liste 10 ile sınırlı değil; istersen 200-300 ISBN biriktirip sonra toplu aratabilirsin."
        )

        manual_text = st.text_area(
            "ISBN listesini yapıştır",
            placeholder="Her satıra veya aralara boşluk koyarak ISBN yazabilirsin.",
            key="ultimate_manual_isbns",
        )
        uploaded_files = st.file_uploader(
            "Barkod fotoğraflarını listeye eklemek için yükle",
            type=["png", "jpg", "jpeg"],
            accept_multiple_files=True,
            key="ultimate_barcode_uploads",
        )
        camera_file = st.camera_input(
            "Kamera ile bir barkod okut ve listeye ekle",
            key=f"ultimate_barcode_camera_{st.session_state['ultimate_camera_nonce']}",
        )

        if st.button("Okunan / Yazılan Barkodları Listeye Ekle", use_container_width=True):
            found_isbns = []
            unreadable = []
            found_isbns.extend(parse_isbns_from_text(manual_text))

            for image_file in list(uploaded_files or []):
                isbn = decode_isbn_from_image(image_file)
                if isbn:
                    found_isbns.append(normalize_lookup_isbn(isbn))
                else:
                    unreadable.append(getattr(image_file, "name", "Yüklenen görsel"))

            if camera_file:
                isbn = decode_isbn_from_image(camera_file)
                if isbn:
                    found_isbns.append(normalize_lookup_isbn(isbn))
                    st.session_state["ultimate_camera_nonce"] += 1
                else:
                    unreadable.append("Kamera görüntüsü")

            add_to_ultimate_barcodes(found_isbns)
            st.session_state["ultimate_lookup_result"] = None
            if found_isbns:
                st.success(f"{len(unique_keep_order(found_isbns))} ISBN listeye eklendi.")
            if unreadable:
                st.warning("Okunamayan görseller: " + ", ".join(unreadable[:8]))

        st.write(f"**Listedeki barkod sayısı:** {len(st.session_state['ultimate_barcodes'])}")
        barcode_editor = st.data_editor(
            [{"isbn": isbn} for isbn in st.session_state["ultimate_barcodes"]],
            num_rows="dynamic",
            use_container_width=True,
            key="ultimate_barcode_editor",
            column_config={"isbn": st.column_config.TextColumn("ISBN")},
        )

        list_col1, list_col2 = st.columns(2)
        with list_col1:
            if st.button("Listeyi Güncelle", use_container_width=True):
                st.session_state["ultimate_barcodes"] = current_ultimate_barcodes_from_editor(barcode_editor)
                st.success("Barkod listesi güncellendi.")
        with list_col2:
            if st.button("Listeyi Temizle", use_container_width=True):
                st.session_state["ultimate_barcodes"] = []
                st.session_state["ultimate_lookup_result"] = None
                st.success("Barkod listesi temizlendi.")

        save_unresolved = st.checkbox(
            "Toplu aramada bulunamayan ISBN'leri Sonra Aranacaklar listesine kaydet",
            value=True,
            key="ultimate_save_unresolved",
        )

        if st.button("Listedeki Barkodları Toplu Arat", type="primary", use_container_width=True):
            isbns = st.session_state.get("ultimate_barcodes", [])
            if not isbns:
                st.warning("Önce barkod listesine ISBN ekle.")
                return

            progress = st.progress(0)
            status_box = st.empty()
            result = {"found": [], "unresolved": [], "existing": [], "errors": []}

            for index, isbn in enumerate(isbns, start=1):
                progress.progress(index / len(isbns))
                status_box.info(f"{index}/{len(isbns)} aranıyor: {isbn}")

                if isbn_exists(isbn):
                    result["existing"].append(isbn)
                    continue

                lookup = get_book_info_comprehensive(
                    isbn,
                    cache_version=LOOKUP_CACHE_VERSION + 1,
                    max_seconds=BULK_LOOKUP_TIMEOUT_SECONDS,
                    web_result_limit=4,
                    retailer_link_limit=4,
                )
                if lookup.get("ok") and lookup.get("book", {}).get("title"):
                    book = lookup["book"]
                    book["isbn"] = isbn
                    result["found"].append(book)
                else:
                    result["unresolved"].append(isbn)
                    if save_unresolved:
                        save_pending_isbn(isbn, note="Ultimate toplu barkod aramasında bulunamadı", source="ultimate_bulk")

            status_box.success("Toplu arama tamamlandı.")
            st.session_state["ultimate_lookup_result"] = result

        result = st.session_state.get("ultimate_lookup_result")
        if not result:
            return

        st.write(f"**Bulunan:** {len(result['found'])} · **Bulunamayan:** {len(result['unresolved'])} · **Zaten kayıtlı:** {len(result['existing'])}")

        if result["found"]:
            found_rows = []
            for book in result["found"]:
                found_rows.append(
                    {
                        "ekle": True,
                        "isbn": clean_text(book.get("isbn")),
                        "title": clean_text(book.get("title")),
                        "author": clean_text(book.get("author")),
                        "publisher": clean_text(book.get("publisher")),
                        "first_print_year": clean_text(book.get("first_print_year")),
                        "page_count": clean_text(book.get("page_count")),
                        "reading_status": clean_text(book.get("reading_status")) or "Okunacak",
                        "category": clean_text(book.get("category")) or "Kategorisiz",
                    }
                )

            edited_found = st.data_editor(
                found_rows,
                use_container_width=True,
                num_rows="fixed",
                key="ultimate_found_editor",
                column_config={
                    "ekle": st.column_config.CheckboxColumn("Ekle"),
                    "isbn": st.column_config.TextColumn("ISBN"),
                    "title": st.column_config.TextColumn("Kitap Adı"),
                    "author": st.column_config.TextColumn("Yazar"),
                    "publisher": st.column_config.TextColumn("Yayınevi"),
                    "first_print_year": st.column_config.TextColumn("Yıl"),
                    "page_count": st.column_config.TextColumn("Sayfa"),
                    "reading_status": st.column_config.SelectboxColumn("Okunma Durumu", options=READING_STATUS_OPTIONS),
                    "category": st.column_config.SelectboxColumn("Kategori", options=CATEGORY_OPTIONS),
                },
            )

            if st.button("Seçili Bulunanları Kütüphaneye Ekle", use_container_width=True):
                rows = editor_rows_to_list(edited_found)
                book_by_isbn = {clean_text(book.get("isbn")): book for book in result["found"]}
                inserted = 0
                skipped = 0
                for row in rows:
                    if not row.get("ekle"):
                        continue
                    isbn = clean_text(row.get("isbn"))
                    book_data = dict(book_by_isbn.get(isbn, blank_book(isbn)))
                    for field in (
                        "isbn",
                        "title",
                        "author",
                        "publisher",
                        "first_print_year",
                        "page_count",
                        "reading_status",
                        "category",
                    ):
                        book_data[field] = row.get(field)

                    if isbn_exists(book_data.get("isbn")):
                        skipped += 1
                        continue
                    if insert_book(book_data):
                        inserted += 1

                st.success(f"{inserted} kitap kütüphaneye eklendi.")
                if skipped:
                    st.warning(f"{skipped} kitap zaten kayıtlı olduğu için atlandı.")

        if result["unresolved"]:
            st.warning("Bulunamayan ISBN'ler:")
            for isbn in result["unresolved"]:
                st.markdown(f"- `{isbn}` · [Google'da ara]({google_search_url_for_isbn(isbn)})")

        if result["existing"]:
            st.info("Zaten kütüphanede olan ISBN'ler:")
            for isbn in result["existing"]:
                st.write(f"- {isbn}")


def render_bulk_add_page():
    st.header("Toplu Kitap Ekle")
    st.caption("25 satırı tek seferde doldurup kaydedebilirsin. Boş satırlar yok sayılır.")

    render_ultimate_barcode_section()
    st.divider()
    render_bulk_barcode_section()
    st.divider()

    auto_lookup = st.checkbox(
        "ISBN yazdığım satırlarda boş bilgileri internetten doldurmayı dene",
        value=True,
    )
    save_unresolved = st.checkbox(
        "Başlığı bulunamayan ISBN'leri sonra aranacaklar listesine ekle",
        value=True,
    )

    initial_rows = blank_bulk_rows(25)
    if st.session_state.get("bulk_prefill_isbn"):
        initial_rows[0]["isbn"] = st.session_state.pop("bulk_prefill_isbn")

    rows = st.data_editor(
        initial_rows,
        num_rows="fixed",
        use_container_width=True,
        hide_index=False,
        key="bulk_books_editor",
        column_config={
            "isbn": st.column_config.TextColumn("ISBN"),
            "title": st.column_config.TextColumn("Kitap Adı"),
            "author": st.column_config.TextColumn("Yazar"),
            "publisher": st.column_config.TextColumn("Yayınevi"),
            "first_print_year": st.column_config.TextColumn("Yıl"),
            "page_count": st.column_config.TextColumn("Sayfa"),
            "reading_status": st.column_config.SelectboxColumn(
                "Okunma Durumu",
                options=READING_STATUS_OPTIONS,
                default="Okunacak",
            ),
            "category": st.column_config.SelectboxColumn(
                "Kategori",
                options=CATEGORY_OPTIONS,
                default="Kategorisiz",
            ),
            "tags": st.column_config.TextColumn("Etiketler"),
            "notes": st.column_config.TextColumn("Not"),
        },
    )

    if not st.button("Toplu Kaydet", type="primary", use_container_width=True):
        return

    inserted = 0
    skipped = 0
    queued = 0
    duplicates = 0
    errors = 0
    seen_isbns = set()

    with st.spinner("Toplu kayıt işleniyor..."):
        for row_number, row in enumerate(editor_rows_to_list(rows), start=1):
            if not row_has_any_book_data(row):
                continue

            raw_isbn = clean_text(row.get("isbn"))
            isbn = normalize_lookup_isbn(raw_isbn)
            if isbn and isbn in seen_isbns:
                duplicates += 1
                continue
            if isbn:
                seen_isbns.add(isbn)

            data, _ = bulk_row_to_book_data(row, auto_lookup=auto_lookup)
            if not data:
                skipped += 1
                if save_unresolved and isbn:
                    if save_pending_isbn(isbn, note=f"Toplu ekleme satırı {row_number}", source="bulk_add"):
                        queued += 1
                continue

            if data.get("isbn") and isbn_exists(data["isbn"]):
                duplicates += 1
                continue

            if insert_book(data):
                inserted += 1
            else:
                errors += 1

    st.success(f"{inserted} kitap eklendi.")
    if queued:
        st.info(f"{queued} ISBN sonra aranacaklar listesine kaydedildi.")
    if skipped:
        st.warning(f"{skipped} satırda kitap adı bulunamadığı için doğrudan eklenmedi.")
    if duplicates:
        st.warning(f"{duplicates} satır tekrar/önceden kayıtlı olduğu için atlandı.")
    if errors:
        st.error(f"{errors} satır kaydedilemedi.")


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
    add_nonce = st.session_state.get("add_nonce", 0)

    success_message = st.session_state.pop("last_add_success", "")
    if success_message:
        st.success(success_message)

    summary = fetch_library_summary()
    metric_col, recent_col = st.columns([0.28, 0.72], vertical_alignment="center")
    with metric_col:
        st.metric("Kütüphanedeki kitap sayısı", summary["count"])
    with recent_col:
        recent_books = summary.get("recent") or []
        if recent_books:
            st.write("**Son eklenenler:**")
            for book in recent_books:
                author = clean_text(book.get("author"))
                suffix = f" - {author}" if author else ""
                st.write(f"• {clean_text(book.get('title'))}{suffix}")
        else:
            st.caption("Henüz kitap eklenmemiş.")

    camera_col, manual_col = st.columns([0.48, 0.52])
    with camera_col:
        img_file = st.camera_input(
            "Barkodu Okutun",
            key=f"barcode_camera_{add_nonce}",
            help="Streamlit kamera bileşeni arka kamerayı kesin zorlayamaz; mobil tarayıcı destekliyorsa arka kamerayı seçebilirsin.",
        )
        uploaded_barcode = st.file_uploader(
            "Barkod fotoğrafı yükle",
            type=["png", "jpg", "jpeg"],
            key=f"barcode_upload_{add_nonce}",
        )
    with manual_col:
        isbn_input = st.text_input(
            "ISBN numarasını manuel girin",
            placeholder="9786052361917",
            key=f"isbn_input_{add_nonce}",
        )
        manual_without_isbn = st.checkbox(
            "ISBN olmadan manuel kitap ekle",
            key=f"manual_without_isbn_{add_nonce}",
        )

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
        normalized_target = normalize_isbn(target_isbn)
        isbn_for_later = normalized_target["isbn13"] if normalized_target else only_digits(target_isbn)
        later_col, search_col = st.columns([0.42, 0.58])
        with later_col:
            if st.button("Bu barkodu sonra aramak için kaydet", use_container_width=True):
                if save_pending_isbn(isbn_for_later, source="single_add"):
                    st.success(f"{isbn_for_later} sonra aranacaklar listesine kaydedildi.")
                    return
        with search_col:
            skip_lookup = st.checkbox("Şimdilik internette arama, sadece barkodu kaydetmek istiyorum", value=False)

        if skip_lookup:
            if st.button("Barkodu listeye kaydet ve çık", type="primary", use_container_width=True):
                if save_pending_isbn(isbn_for_later, source="single_add_skip_lookup"):
                    st.success(f"{isbn_for_later} sonra aranacaklar listesine kaydedildi.")
            return

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
            st.markdown(f"[Bu ISBN'i Google'da ara]({google_search_url_for_isbn(target_isbn)})")
            if st.button("Bulunamadı, sonra aranacaklar listesine kaydet", use_container_width=True):
                if save_pending_isbn(target_isbn, note="Tekil ekleme ekranında bulunamadı", source="lookup_failed"):
                    st.success("ISBN sonra aranacaklar listesine kaydedildi.")

    if manual_without_isbn and not book_info:
        book_info = blank_book("")

    if not book_info:
        return

    if book_info.get("cover_url"):
        st.image(book_info["cover_url"], width=140)

    with st.form("save_book_form"):
        st.subheader("Kitap Detayları")
        submit_top = st.form_submit_button(
            "Kütüphaneye Kaydet",
            use_container_width=True,
            key=f"save_book_top_{add_nonce}",
        )
        data = build_form_data(f"new_book_{add_nonce}", book_info)
        if target_isbn:
            normalized = normalize_isbn(target_isbn)
            data["isbn"] = normalized["isbn13"] if normalized else only_digits(target_isbn)
        submit_bottom = st.form_submit_button(
            "Kütüphaneye Kaydet",
            use_container_width=True,
            key=f"save_book_bottom_{add_nonce}",
        )
        submitted = submit_top or submit_bottom

    if submitted:
        if not clean_text(data.get("title")):
            st.error("Kitap adı zorunlu.")
            return

        existing = isbn_exists(data.get("isbn"))
        if existing:
            st.warning(f"Bu ISBN zaten kayıtlı: {existing.get('title', 'İsimsiz')}")
            return

        if insert_book(data):
            st.session_state["last_add_success"] = f"'{clean_text(data['title'])}' kütüphaneye eklendi."
            set_page("add")
            st.session_state["add_nonce"] = add_nonce + 1
            st.rerun()


def render_lookup_queue_page():
    st.header("Sonra Aranacak Barkodlar")
    st.caption("Bulunması uzun süren veya o anda eklemek istemediğin ISBN'leri burada saklayıp sonra tekrar aratabilirsin.")

    pending_items = fetch_pending_isbns()
    if not pending_items:
        st.info("Sonra aranacak ISBN yok.")
        return

    for item in pending_items:
        queue_id = item.get("id")
        isbn = clean_text(item.get("isbn"))
        status = clean_text(item.get("status")) or "bekliyor"
        note = clean_text(item.get("note"))

        with st.expander(f"{isbn} · {status}"):
            st.write(f"**ISBN:** {isbn}")
            if note:
                st.write(f"**Not:** {note}")
            st.markdown(f"[Google'da ara]({google_search_url_for_isbn(isbn)})")

            action_col1, action_col2, action_col3 = st.columns(3)
            with action_col1:
                if st.button("Uygulamada Tekrar Ara", key=f"queue_search_{queue_id}", use_container_width=True):
                    with st.spinner(f"{isbn} yeniden aranıyor..."):
                        lookup = get_book_info_comprehensive(isbn, cache_version=LOOKUP_CACHE_VERSION + 1)
                    if lookup.get("ok") and lookup.get("book", {}).get("title"):
                        st.session_state[f"queue_result_{queue_id}"] = lookup["book"]
                        update_pending_isbn_status(queue_id, "bulundu")
                        st.success("Kitap bulundu. Aşağıdan kontrol edip ekleyebilirsin.")
                    else:
                        update_pending_isbn_status(queue_id, "bulunamadı")
                        st.warning("Uygulama güvenilir kayıt bulamadı. Google linkinden kontrol edip elle ekleyebilirsin.")
            with action_col2:
                if st.button("Elle Eklemeye Gönder", key=f"queue_manual_{queue_id}", use_container_width=True):
                    st.session_state["bulk_prefill_isbn"] = isbn
                    set_page("bulk_add")
                    st.rerun()
            with action_col3:
                if st.button("Listeden Sil", key=f"queue_delete_{queue_id}", use_container_width=True):
                    if delete_pending_isbn(queue_id):
                        st.success("ISBN listeden silindi.")
                        st.rerun()

            result = st.session_state.get(f"queue_result_{queue_id}")
            if result:
                if result.get("cover_url"):
                    st.image(result["cover_url"], width=120)
                with st.form(f"queue_add_form_{queue_id}"):
                    data = build_form_data(f"queue_{queue_id}", result)
                    data["isbn"] = normalize_lookup_isbn(isbn)
                    submitted = st.form_submit_button("Bu Kitabı Kütüphaneye Ekle", use_container_width=True)

                if submitted:
                    if isbn_exists(data.get("isbn")):
                        st.warning("Bu ISBN zaten kütüphanede kayıtlı.")
                    elif insert_book(data):
                        update_pending_isbn_status(queue_id, "eklendi")
                        delete_pending_isbn(queue_id)
                        st.success("Kitap kütüphaneye eklendi ve kuyruktan kaldırıldı.")
                        st.rerun()


# --- UYGULAMA ---
inject_css()

if "page" not in st.session_state:
    st.session_state["page"] = "add"
if "add_nonce" not in st.session_state:
    st.session_state["add_nonce"] = 0

filters = render_sidebar()
all_books = filters[5]
render_top_bar()

if st.session_state.get("page") == "add":
    render_add_page()
elif st.session_state.get("page") == "bulk_add":
    render_bulk_add_page()
elif st.session_state.get("page") == "lookup_queue":
    render_lookup_queue_page()
else:
    render_library_page(all_books, filters[:5])
