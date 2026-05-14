import json
import re
from urllib.parse import urljoin

import requests
import streamlit as st
from bs4 import BeautifulSoup
from PIL import Image
from pyzbar.pyzbar import decode
from supabase import Client, create_client


# --- KONFIGURASYON VE BAGLANTI ---
st.set_page_config(page_title="MinisKitapApp", page_icon="📚", layout="wide")

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}

REQUEST_TIMEOUT = 8

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

# Supabase books tablonuzdaki mevcut kolonlar. Ek kolon eklemeden guvenli kayit yapar.
SAVE_FIELDS = [
    "isbn",
    "title",
    "author",
    "translator",
    "publisher",
    "page_count",
    "paper_type",
    "first_print_year",
    "category",
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
        raise RuntimeError("SUPABASE_URL veya SUPABASE_KEY secret olarak tanimli degil.")
    return create_client(url, key)


try:
    supabase: Client = init_connection()
except Exception as exc:
    st.error(f"Supabase baglantisi kurulamadi: {exc}")
    st.stop()


# --- ISBN NORMALIZASYONU ---
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
    return book


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


def unique_keep_order(values) -> list:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def normalize_isbn(raw_value: str) -> dict | None:
    """Barkoddan veya manuel giristen ISBN-13/ISBN-10 varyasyonlarini uretir."""
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

    # Son sans: checksum bozuk olsa bile 978/979 EAN'i dene, ama kullaniciya uyar.
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
            "publisher": clean_text(info.get("publisher")),
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
            "publisher": clean_text([p.get("name") for p in entry.get("publishers", [])]),
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
            "publisher": clean_text(edition.get("publishers", [])),
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
            "publisher": clean_text((doc.get("publisher") or [])[:2]),
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
                "fields": "title,author_name,publisher,first_publish_year,number_of_pages_median,isbn,cover_i,language,subject,key",
            },
        )
        for doc in (search or {}).get("docs", []):
            record = openlibrary_search_record(doc, variants[0], variants)
            if record:
                records.append(record)
    return records


# --- OPSIYONEL KAYNAK 3: ISBNDB ---
def search_isbndb(variants: list[str]) -> list[dict]:
    """Streamlit secrets icine ISBNDB_API_KEY eklerseniz devreye girer."""
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
                    "publisher": clean_text(book_data.get("publisher")),
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
            "publisher": person_or_org_to_text(obj.get("publisher")),
            "description": clean_text(obj.get("description")),
            "cover_url": clean_text(obj.get("image")),
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
            "publisher": line_value(lines, ["Yayınevi", "Yayıncı", "Yayinevi", "Publisher"]),
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
        "cover_url": 1,
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
            "error": "Gecerli bir ISBN-10 veya ISBN-13 bulunamadi.",
            "normalized": None,
            "book": blank_book(only_digits(raw_isbn)),
            "records_count": 0,
        }

    variants = normalized["variants"]
    records = []

    # Resmi / stabil kaynaklar once.
    records.extend(search_google_books(variants))
    records.extend(search_openlibrary(variants))
    records.extend(search_isbndb(variants))

    merged = merge_records(normalized, records)

    # Kayit yoksa veya eksikse Turkce perakende sitelerini son sans olarak tara.
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
        "error": "Online kaynaklarda guvenilir kitap kaydi bulunamadi.",
        "normalized": normalized,
        "book": blank_book(normalized.get("isbn13") or variants[0]),
        "records_count": len(records),
    }


# --- KENAR CUBUGU ---
st.sidebar.title("📚 MinisKitapApp")
menu = st.sidebar.radio("Menü", ["🏠 Kütüphanem", "🔍 Kitap Ekle"])

st.sidebar.divider()
st.sidebar.subheader("Kişisel Filtreler")
p_filter = st.sidebar.selectbox(
    "Görünüm",
    ["Tümü", "Favoriler", "Sıradaki (Okunacak)", "Ödünç Verilenler", "Okuma Durumu"],
)
st.sidebar.subheader("Kitap Filtreleri")
sort_by = st.sidebar.selectbox("Sıralama", ["İsim (A-Z)", "Yazar (A-Z)", "Yeni Eklenenler"])


# --- ANA SAYFA: KUTUPHANEM ---
if menu == "🏠 Kütüphanem":
    st.header("Kitaplığım")

    try:
        query = supabase.table("books").select("*")
        if sort_by == "İsim (A-Z)":
            query = query.order("title")
        elif sort_by == "Yazar (A-Z)":
            query = query.order("author")
        else:
            query = query.order("created_at", desc=True)
        books = query.execute().data
    except Exception as exc:
        st.error(f"Kitaplar yuklenemedi: {exc}")
        books = []

    if not books:
        st.info("Kütüphanenizde henüz kitap bulunmuyor.")
    else:
        for book in books:
            title = book.get("title") or "İsimsiz"
            author = book.get("author") or "Yazar Bilinmiyor"
            with st.expander(f"📖 {title} - {author}"):
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.write(f"**ISBN:** {book.get('isbn', '-')}")
                    st.write(f"**Yayınevi:** {book.get('publisher', '-')}")
                    st.write(f"**Çevirmen:** {book.get('translator', '-')}")
                    st.write(f"**Sayfa Sayısı:** {book.get('page_count', '-')}")
                with col2:
                    st.write(f"**Hamur:** {book.get('paper_type', '-')}")
                    st.write(f"**Baskı Yılı:** {book.get('first_print_year', '-')}")
                    st.write(f"**Kategori:** {book.get('category', '-')}")
                with col3:
                    if st.button("🗑️ Sil", key=f"del_{book['id']}"):
                        try:
                            supabase.table("books").delete().eq("id", book["id"]).execute()
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Silme islemi basarisiz: {exc}")


# --- ANA SAYFA: KITAP EKLE ---
elif menu == "🔍 Kitap Ekle":
    st.header("Yeni Kitap Ekle")

    img_file = st.camera_input("Barkodu Okutun")
    isbn_input = st.text_input("Veya ISBN numarasını manuel girin (Örn: 9786052361917)")

    target_isbn = None

    if img_file:
        image = Image.open(img_file).convert("RGB")
        decoded_items = decode(image)
        if decoded_items:
            for item in decoded_items:
                candidate = item.data.decode("utf-8", errors="ignore").strip()
                if normalize_isbn(candidate):
                    target_isbn = candidate
                    break
            if target_isbn:
                st.success(f"Kameradan barkod okundu: {target_isbn}")
            else:
                st.warning("Barkod okundu ama geçerli bir ISBN/EAN-13 gibi görünmüyor.")
        else:
            st.warning("Barkod okunamadı. Kamerayı daha yakından ve net ışıkta tekrar deneyin.")

    if isbn_input:
        target_isbn = isbn_input.strip()

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
                "ISBN hazır; başlığı elle girip yine de kütüphaneye kaydedebilirsiniz."
            )

        if book_info.get("cover_url"):
            st.image(book_info["cover_url"], width=140)

        with st.form("save_book_form"):
            st.subheader("Kitap Detayları")

            c1, c2 = st.columns(2)
            with c1:
                m_title = st.text_input("Kitap Adı*", value=book_info.get("title", ""))
                m_author = st.text_input("Yazar", value=book_info.get("author", ""))
                m_translator = st.text_input("Çevirmen", value=book_info.get("translator", ""))
                m_publisher = st.text_input("Yayınevi", value=book_info.get("publisher", ""))
            with c2:
                m_pages = st.text_input("Sayfa Sayısı", value=book_info.get("page_count", ""))
                m_paper = st.text_input("Hamur Tipi", value=book_info.get("paper_type", ""))
                m_year = st.text_input("İlk Baskı Yılı", value=book_info.get("first_print_year", ""))
                m_cat = st.selectbox("Kategori", ["Kategorisiz", "Kişisel", "Profesyonel"])

            submit = st.form_submit_button("Kütüphaneye Kaydet")

            if submit:
                if not clean_text(m_title):
                    st.error("Kitap adı zorunlu.")
                else:
                    isbn_to_save = clean_text(book_info.get("isbn") or target_isbn)
                    final_data = {
                        "isbn": isbn_to_save,
                        "title": clean_text(m_title),
                        "author": clean_text(m_author),
                        "translator": clean_text(m_translator),
                        "publisher": clean_text(m_publisher),
                        "page_count": clean_text(m_pages),
                        "paper_type": clean_text(m_paper),
                        "first_print_year": clean_text(m_year),
                        "category": m_cat,
                    }
                    final_data = {key: final_data[key] for key in SAVE_FIELDS if key in final_data}

                    try:
                        existing = (
                            supabase.table("books")
                            .select("id,title")
                            .eq("isbn", isbn_to_save)
                            .limit(1)
                            .execute()
                            .data
                        )
                        if existing:
                            st.warning(f"Bu ISBN zaten kayıtlı: {existing[0].get('title', 'İsimsiz')}")
                        else:
                            supabase.table("books").insert(final_data).execute()
                            st.success(f"'{final_data['title']}' kütüphaneye eklendi!")
                    except Exception as exc:
                        st.error(f"Kayit islemi basarisiz: {exc}")
