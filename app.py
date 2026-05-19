import base64
import csv
import html
import io
import json
import random
import re
import time
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
import streamlit as st
import streamlit.components.v1 as components
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
APP_NAME = "Badger's Book App"
BRAND_IMAGE_PATHS = [
    APP_DIR / "assets" / "badger.png",
    APP_DIR / "badger.png",
]
STATIC_LIBRARY_DIR = APP_DIR / "static_library"
STATIC_LIBRARY_JSON = STATIC_LIBRARY_DIR / "books.json"
STATIC_LIBRARY_COVERS_DIR = STATIC_LIBRARY_DIR / "covers"
STATIC_LIBRARY_ZIP = APP_DIR / "static_library.zip"
STATIC_LIBRARY_ZIP_CACHE_DIR = APP_DIR / ".static_library_cache"

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
LOOKUP_CACHE_VERSION = 8
BULK_LOOKUP_TIMEOUT_SECONDS = 20

PAGE_LABELS = {
    "home": "✨ Ana Ekran",
    "library": "🏠 Kütüphanem",
    "detail_library": "📚 Detaylı Kütüphanem",
    "add": "🔍 Kitap Ekle",
    "wishlist": "💫 Wishlist",
    "recommendations": "🎁 Tavsiyeler",
    "game": "🎮 Oyun Oyna",
    "bulk_add": "🧾 Toplu Kitap Ekle",
    "lookup_queue": "⏳ Sonra Aranacaklar",
    "missing_info": "🧩 Eksik Bilgiler",
    "quick_search": "⚡ Hızlı Arama",
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
    "current_page",
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
    "estimated_price",
    "estimated_price_source",
    "estimated_price_checked_at",
]

SAVE_FIELDS = [
    "isbn",
    "title",
    "author",
    "translator",
    "publisher",
    "page_count",
    "current_page",
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
    "estimated_price",
    "estimated_price_source",
    "estimated_price_checked_at",
    "category",
    "reading_status",
    "o_da_okudu",
    "reading_started_at",
    "reading_finished_at",
    "reread_wanted",
    "reading_comment",
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
    ("Kitapseç", "https://www.kitapsec.com/Arama/index.php?a={isbn}"),
    ("Idefix", "https://www.idefix.com/search?q={isbn}"),
    ("İkra Kitap", "https://www.ikrakitap.com/arama?q={isbn}"),
    ("İmge", "https://www.imge.com.tr/arama?q={isbn}"),
    ("Amazon TR", "https://www.amazon.com.tr/s?k={isbn}"),
]

SITE_SPECIFIC_RETAILERS = {
    "kitapsec": ("Kitapseç", "https://www.kitapsec.com/Arama/index.php?a={isbn}"),
    "kitapyurdu": ("Kitapyurdu", "https://www.kitapyurdu.com/index.php?route=product/search&filter_name={isbn}"),
}

SITE_EXTRA_SEARCH_TEMPLATES = {
    "Kitapseç": [
        "https://www.kitapsec.com/Arama/index.php?key={isbn}",
        "https://www.kitapsec.com/Mch.php?a={isbn}",
        "https://www.kitapsec.com/mobil/arama.php?a={isbn}",
    ],
}

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
    book["current_page"] = ""
    book["o_da_okudu"] = False
    book["reading_started_at"] = ""
    book["reading_finished_at"] = ""
    book["reread_wanted"] = False
    book["reading_comment"] = ""
    book["favorite"] = False
    book["tags"] = []
    book["notes"] = ""
    book["loaned_to"] = ""
    book["rating"] = None
    book["estimated_price"] = None
    book["estimated_price_source"] = ""
    book["estimated_price_checked_at"] = ""
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


def parse_turkish_price(value: str) -> float | None:
    value = clean_text(value)
    if not value:
        return None
    if re.fullmatch(r"\d{1,6}(?:\.\d{1,2})?", value):
        try:
            price = float(value)
            return round(price, 2) if 0 < price <= 100000 else None
        except Exception:
            return None
    match = re.search(r"(?:₺|TL)?\s*(\d{1,4}(?:[.\s]\d{3})*(?:,\d{1,2})|\d{1,5}(?:\.\d{1,2})?)\s*(?:TL|₺)", value, re.I)
    if not match:
        match = re.search(r"(\d{1,4}(?:[.\s]\d{3})*(?:,\d{1,2}))", value)
    if not match:
        return None
    number = match.group(1).replace(" ", "")
    if "," in number:
        number = number.replace(".", "").replace(",", ".")
    try:
        price = float(number)
    except Exception:
        return None
    if price <= 0 or price > 100000:
        return None
    return round(price, 2)


def format_tl(value) -> str:
    try:
        amount = float(value or 0)
    except Exception:
        amount = 0
    return f"{amount:,.2f} TL".replace(",", "X").replace(".", ",").replace("X", ".")


def extract_price_from_text(text: str, source: str = "") -> float | None:
    text = clean_text(text)
    source_key = canonical_key(source)
    patterns = [
        r"Kitapyurdu Fiyatı\s*:?\s*(?:₺|TL)?\s*([\d.,]+)",
        r"Kitapseç Fiyatı\s*:?\s*(?:₺|TL)?\s*([\d.,]+)",
        r"KitapSeç Fiyatı\s*:?\s*(?:₺|TL)?\s*([\d.,]+)",
        r"Sepette\s*(?:₺|TL)?\s*([\d.,]+)",
        r"Sepette\s*([\d.,]+)\s*TL",
        r"İndirimli Fiyat.*?(?:₺|TL)?\s*([\d.,]+)",
        r"Fiyatı\s*:?\s*(?:₺|TL)?\s*([\d.,]+)",
        r"Liste Fiyatı\s*:?\s*(?:₺|TL)?\s*([\d.,]+)",
        r"(?:₺|TL)\s*([\d.,]+)",
        r"([\d.,]+)\s*(?:TL|₺)",
    ]
    prices = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I | re.S):
            price = parse_turkish_price(match.group(0))
            if price:
                prices.append(price)
    if not prices:
        return None
    if any(token in source_key for token in ("kitapyurdu", "kitapsec", "dr", "imge", "bkm", "kitapsepeti")):
        return min(prices)
    return min(prices)


def is_noise_text(value: str) -> bool:
    value = clean_text(value)
    if not value:
        return True
    key = canonical_key(value)
    if not key:
        return True
    exact_noise = {
        "listesi",
        "lar",
        "ler",
        "lari",
        "leri",
        "kitap",
        "kitaplar",
        "yazarlar",
        "yayinevleri",
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
        "alisveris listesi",
        "favorilerime ekle",
        "fiyat alarmi ekle",
        "haber ver",
        "hizli siparis",
        "sepete ekle",
        "hemen al",
        "basa don",
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


def normalize_date_text(value) -> str:
    value = clean_text(value)
    if not value:
        return ""
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except Exception:
            pass
    return value


def days_between_dates(start_value, end_value) -> int | None:
    start = normalize_date_text(start_value)
    end = normalize_date_text(end_value)
    if not start or not end:
        return None
    try:
        start_date = datetime.strptime(start, "%Y-%m-%d").date()
        end_date = datetime.strptime(end, "%Y-%m-%d").date()
    except Exception:
        return None
    days = (end_date - start_date).days + 1
    return days if days > 0 else None


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
    normalized["current_page"] = clean_text(only_digits(normalized.get("current_page")) or normalized.get("current_page"))
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
    normalized["estimated_price"] = normalized.get("estimated_price") or None
    normalized["estimated_price_source"] = clean_text(normalized.get("estimated_price_source"))
    normalized["estimated_price_checked_at"] = clean_text(normalized.get("estimated_price_checked_at")) or None
    normalized["category"] = clean_text(normalized.get("category")) or "Kategorisiz"
    normalized["reading_status"] = clean_text(normalized.get("reading_status")) or "Okunacak"
    normalized["o_da_okudu"] = bool(normalized.get("o_da_okudu"))
    normalized["reading_started_at"] = normalize_date_text(normalized.get("reading_started_at"))
    normalized["reading_finished_at"] = normalize_date_text(normalized.get("reading_finished_at"))
    normalized["reread_wanted"] = bool(normalized.get("reread_wanted"))
    normalized["reading_comment"] = clean_text(normalized.get("reading_comment"))
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

def resolve_static_cover_path(value: str) -> str:
    value = clean_text(value)
    if not value:
        return ""
    if value.startswith("zip://"):
        return extract_static_zip_cover(value.removeprefix("zip://"))
    if value.startswith(("http://", "https://", "data:")):
        return value
    path = Path(value)
    if not path.is_absolute():
        path = APP_DIR / value
    return str(path) if path.exists() else value


def extract_static_zip_cover(member_name: str) -> str:
    member_name = clean_text(member_name).lstrip("/")
    if not member_name or not STATIC_LIBRARY_ZIP.exists():
        return ""
    try:
        with zipfile.ZipFile(STATIC_LIBRARY_ZIP) as archive:
            if member_name not in archive.namelist():
                return ""
            target = STATIC_LIBRARY_ZIP_CACHE_DIR / "covers" / Path(member_name).name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(archive.read(member_name))
            return str(target)
    except Exception:
        return ""


@st.cache_data(ttl=60 * 60, show_spinner=False)
def load_static_library_books() -> list[dict]:
    if STATIC_LIBRARY_JSON.exists():
        try:
            payload = json.loads(STATIC_LIBRARY_JSON.read_text(encoding="utf-8"))
        except Exception:
            return []
        books = payload.get("books", payload if isinstance(payload, list) else [])
        if not isinstance(books, list):
            return []
        resolved = []
        for book in books:
            if not isinstance(book, dict):
                continue
            item = dict(book)
            local_cover = clean_text(item.get("local_cover_path"))
            if local_cover:
                local_path = APP_DIR / local_cover
                if local_path.exists():
                    item.setdefault("remote_cover_url", clean_text(item.get("cover_url")))
                    item["cover_url"] = str(local_path)
            elif clean_text(item.get("cover_url")):
                item["cover_url"] = resolve_static_cover_path(item.get("cover_url"))
            resolved.append(item)
        return resolved

    if not STATIC_LIBRARY_ZIP.exists():
        return []

    try:
        with zipfile.ZipFile(STATIC_LIBRARY_ZIP) as archive:
            names = set(archive.namelist())
            json_name = "static_library/books.json" if "static_library/books.json" in names else "books.json"
            if json_name not in names:
                return []
            payload = json.loads(archive.read(json_name).decode("utf-8"))
            books = payload.get("books", payload if isinstance(payload, list) else [])
            if not isinstance(books, list):
                return []

            resolved = []
            for book in books:
                if not isinstance(book, dict):
                    continue
                item = dict(book)
                local_cover = clean_text(item.get("local_cover_path"))
                if local_cover and local_cover in names:
                    item.setdefault("remote_cover_url", clean_text(item.get("cover_url")))
                    item["cover_url"] = f"zip://{local_cover}"
                elif clean_text(item.get("cover_url")):
                    item["cover_url"] = resolve_static_cover_path(item.get("cover_url"))
                resolved.append(item)
            return resolved
    except Exception:
        return []


def static_library_status_text() -> str:
    books = load_static_library_books()
    if not books:
        return "GitHub hizli cache aktif degil."
    source = "klasor" if STATIC_LIBRARY_JSON.exists() else "ZIP"
    return (
        f"GitHub hizli cache aktif ({source}): {len(books)} kitap/kapak yerelden okunuyor; "
        "okunma durumu, not ve puan gibi degisen alanlar Supabase'den canli bindiriliyor."
    )


def find_static_book_by_isbn(isbn: str) -> dict | None:
    lookup = normalize_isbn(isbn)
    variants = lookup.get("variants", []) if lookup else [only_digits(isbn)]
    variant_digits = {only_digits(variant) for variant in variants if only_digits(variant)}
    if not variant_digits:
        return None
    for book in load_static_library_books():
        book_digits = only_digits(book.get("isbn"))
        if book_digits and book_digits in variant_digits:
            return book
    return None




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


def strip_publisher_from_title(title: str, publisher: str) -> str:
    title = clean_text(title)
    publisher = clean_text(publisher)
    if not title or not publisher:
        return title

    for suffix in unique_keep_order([publisher, normalize_publisher_name(publisher)]):
        suffix = clean_text(suffix)
        if suffix and title.casefold().endswith(suffix.casefold()):
            stripped = clean_text(title[: -len(suffix)])
            return stripped or title

        title_words = title.split()
        suffix_words = suffix.split()
        if suffix_words and len(title_words) > len(suffix_words):
            trailing = " ".join(title_words[-len(suffix_words) :])
            if canonical_key(trailing) == canonical_key(suffix):
                stripped = " ".join(title_words[: -len(suffix_words)])
                return clean_text(stripped) or title

    return title


def schema_value_to_text(value) -> str:
    if isinstance(value, dict):
        return clean_text(
            value.get("unitText")
            or value.get("value")
            or value.get("description")
            or value.get("name")
            or value.get("@id")
        )
    return clean_text(value)


def schema_additional_properties(obj: dict) -> dict:
    mapped = {}
    properties = obj.get("additionalProperty") or obj.get("additionalProperties") or []
    if isinstance(properties, dict):
        properties = [properties]
    if not isinstance(properties, list):
        return mapped

    for prop in properties:
        if not isinstance(prop, dict):
            continue
        name = canonical_key(prop.get("name"))
        value = schema_value_to_text(
            prop.get("unitText")
            or prop.get("value")
            or prop.get("description")
            or prop.get("text")
        )
        if not name or not value or is_noise_text(value):
            continue

        if "yazar" in name or name == "author":
            mapped["author"] = dedupe_comma_values(value)
        elif "cevirmen" in name or "translator" in name:
            mapped["translator"] = dedupe_comma_values(value)
        elif "yayinevi" in name or "yayinci" in name or name in {"publisher", "marka", "brand"}:
            mapped["publisher"] = normalize_publisher_name(value)
        elif "sayfa" in name or name == "pages":
            mapped["page_count"] = clean_text(only_digits(value) or value)
        elif "ebat" in name or "boyut" in name or "dimension" in name:
            mapped["dimensions"] = value
        elif "kategori" in name or name in {"tur", "konu", "genre", "category"}:
            mapped["genre"] = value
        elif "hamur" in name or "kagit" in name or "cilt" in name:
            mapped["paper_type"] = value
        elif "baski sayisi" in name or name == "baski":
            mapped["print_edition"] = value
        elif "basim tarihi" in name or "yayin tarihi" in name or "ilk baski yili" in name:
            mapped["first_print_year"] = first_year(value)
            mapped["published_date"] = value
        elif name in {"dil", "yayin dili", "kitap dili"}:
            mapped["language"] = pretty_language(value)

    return mapped


def jsonld_offer_price(obj: dict) -> float | None:
    offers = obj.get("offers") or obj.get("offer") or {}
    offer_items = offers if isinstance(offers, list) else [offers]
    for offer in offer_items:
        if not isinstance(offer, dict):
            continue
        for key in ("price", "lowPrice", "highPrice"):
            price = parse_turkish_price(clean_text(offer.get(key)))
            if price:
                return price
    return None


def extract_price_from_soup(soup: BeautifulSoup, source: str = "") -> float | None:
    for obj in jsonld_objects(soup):
        if isinstance(obj, dict):
            price = jsonld_offer_price(obj)
            if price:
                return price
    return extract_price_from_text(soup.get_text(" "), source)



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

    schema_props = schema_additional_properties(obj)
    author = person_or_org_to_text(obj.get("author"))
    publisher = (
        person_or_org_to_text(obj.get("publisher"))
        or person_or_org_to_text(obj.get("brand"))
        or schema_props.get("publisher", "")
    )

    title = strip_publisher_from_title(obj.get("name") or obj.get("headline"), publisher)
    if not title or is_bad_title(title, variants, source):
        return None

    price = jsonld_offer_price(obj)

    book = blank_book(variants[0])
    book.update(
        {
            "title": title,
            "author": dedupe_comma_values(author),
            "publisher": normalize_publisher_name(publisher),
            "description": clean_text(obj.get("description")),
            "cover_url": image_to_url(obj.get("image")),
            "source_url": url,
            "estimated_price": price,
            "estimated_price_source": source if price else "",
            "estimated_price_checked_at": datetime.now(timezone.utc).isoformat() if price else "",
            "_source": source,
            "_isbn_matched": isbn_matched,
        }
    )
    for field, value in schema_props.items():
        if value and (not book.get(field) or field in {"author", "publisher", "page_count", "genre"}):
            book[field] = value
    book["title"] = strip_publisher_from_title(book.get("title"), book.get("publisher"))
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
        "yazarlar",
        "yayınevi",
        "yayınevleri",
        "yayıncı",
        "çevirmen",
        "sayfa sayısı",
        "hamur tipi",
        "ebat",
        "baskı sayısı",
        "dil",
        "isbn",
    }
    blocked_next_keys = {canonical_key(word) for word in blocked_next_words}
    for index, line in enumerate(lines):
        folded = line.casefold()
        for label, folded_label in zip(labels, label_set):
            if folded.startswith(folded_label):
                rest = line[len(label):]
                if rest and not rest[:1].isspace() and rest[:1] not in ":;-":
                    continue
                value = clean_text(rest.strip(" :;-"))
                if (
                    value
                    and canonical_key(value) not in blocked_next_keys
                    and not is_noise_text(value)
                    and len(value) < 150
                ):
                    return value
                if index + 1 < len(lines):
                    next_line = clean_text(lines[index + 1])
                    if (
                        next_line
                        and canonical_key(next_line) not in blocked_next_keys
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
    if looks_like_search_page(url, title_hint):
        return None

    for index, line in enumerate(lines):
        if not variant_in_text(variants, line):
            continue

        before = clean_context_lines(lines[max(0, index - 10) : index])
        after = clean_context_lines(lines[index + 1 : index + 10])
        window = before + [line] + after

        author = line_value(window, ["Yazar", "Author"])
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


def kitapsec_detail_value(details: dict, *labels: str) -> str:
    wanted = [canonical_key(label) for label in labels]
    for label, value in details.items():
        key = canonical_key(label)
        if any(key == item or item in key for item in wanted):
            return clean_field_value(value)
    return ""


def record_from_kitapsec_html(soup: BeautifulSoup, variants: list[str], source: str, url: str) -> dict | None:
    labels = soup.find_all("div", class_="baslikText")
    values = soup.find_all("div", class_="sonucText")
    details = {}
    for label, value in zip(labels, values):
        label_text = clean_text(label.get_text(" "))
        value_text = clean_text(value.get_text(" "))
        if label_text and value_text:
            details[label_text] = value_text
    if not details:
        return None
    page_text = soup.get_text(" ")
    detail_isbn = kitapsec_detail_value(details, "ISBN / BARKOD", "ISBN", "Barkod")
    if detail_isbn and not variant_in_text(variants, detail_isbn):
        return None
    if not detail_isbn and not variant_in_text(variants, page_text):
        return None
    publisher = normalize_publisher_name(kitapsec_detail_value(details, "Yayınevi / Marka", "Yayinevi / Marka", "Yayınevi", "Yayıncı", "Marka"))
    h1 = soup.find("h1")
    title = clean_text(h1.get_text(" ") if h1 else meta_content(soup, "og:title", "twitter:title"))
    title = strip_publisher_from_title(title, publisher)
    published_date = kitapsec_detail_value(details, "Basım Tarihi", "Yayın Tarihi", "Yayın Yılı", "Basım Yılı", "İlk Baskı Yılı")
    page_count = kitapsec_detail_value(details, "Sayfa Sayısı", "Sayfa")
    cover_url = meta_content(soup, "og:image", "twitter:image")
    if cover_url:
        cover_url = urljoin(url, cover_url)
    price = extract_price_from_soup(soup, source)
    book = blank_book(variants[0])
    book.update({
        "title": title,
        "author": dedupe_comma_values(kitapsec_detail_value(details, "Yazar")),
        "translator": dedupe_comma_values(kitapsec_detail_value(details, "Çevirmen", "Cevirmen")),
        "publisher": publisher,
        "page_count": clean_text(only_digits(page_count) or page_count),
        "paper_type": kitapsec_detail_value(details, "Hamur Tipi", "Kağıt", "Kağıt Cinsi", "Kagit Cinsi"),
        "dimensions": kitapsec_detail_value(details, "Kitap Ebatı", "Ebat", "Boyut", "Kitap Boyutu"),
        "first_print_year": first_year(published_date),
        "published_date": published_date,
        "print_edition": kitapsec_detail_value(details, "Baskı", "Baskı Sayısı", "Baski Sayisi"),
        "language": pretty_language(kitapsec_detail_value(details, "Dil", "Yayın Dili", "Kitap Dili")),
        "genre": kitapsec_detail_value(details, "Kategori", "Tür", "Konu"),
        "cover_url": cover_url,
        "source_url": url,
        "estimated_price": price,
        "estimated_price_source": source if price else "",
        "estimated_price_checked_at": datetime.now(timezone.utc).isoformat() if price else "",
        "_source": source,
        "_isbn_matched": True,
    })
    return sanitize_scraped_record(book, variants, source)



def extract_book_from_html(html: str, variants: list[str], source: str, url: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    if "kitapsec" in canonical_key(source):
        kitapsec_record = record_from_kitapsec_html(soup, variants, source, url)
        if kitapsec_record:
            return kitapsec_record
    page_text = soup.get_text(" ")
    page_price = extract_price_from_soup(soup, source)
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

    if not search_page:
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
            "author": line_value(lines, ["Yazar"]),
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
            "estimated_price": page_price,
            "estimated_price_source": source if page_price else "",
            "estimated_price_checked_at": datetime.now(timezone.utc).isoformat() if page_price else "",
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
        "siparis",
        "hizlisiparis",
        "checkout",
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
        image_text = " ".join(
            clean_text(img.get("alt") or img.get("title"))
            for img in anchor.find_all("img")
        )
        link_text = clean_text(anchor.get_text(" ") or anchor.get("title") or image_text)
        product_like = "/products/" in folded or "/kitap/" in folded or "/mobil/" in folded
        if is_noise_text(link_text) and not product_like:
            continue
        score = 0
        if any(variant and variant in only_digits(full_url + " " + link_text) for variant in variants):
            score += 10
        if product_like:
            score += 8
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


def is_kitapsec_product_url(url: str) -> bool:
    path = urlparse(url).path.casefold()
    return ("/products/" in path and path.endswith(".html")) or ("/mobil/" in path and "_urn" in path and path.endswith(".html"))


def kitapsec_product_links(soup: BeautifulSoup, base_url: str, variants: list[str]) -> list[str]:
    matched = []
    others = []
    for anchor in soup.find_all("a", href=True):
        full_url = urljoin(base_url, anchor["href"].strip())
        if not is_kitapsec_product_url(full_url):
            continue
        link_text = clean_text(anchor.get_text(" "))
        target = f"{full_url} {link_text}"
        if variant_in_text(variants, target):
            matched.append(full_url)
        else:
            others.append(full_url)
    return unique_keep_order([*matched, *others])



def search_specific_retailer(
    variants: list[str],
    source: str,
    template: str,
    deadline: float | None = None,
    link_limit: int = 12,
) -> list[dict]:
    records = []
    seen_links = set()
    search_templates = unique_keep_order([template, *SITE_EXTRA_SEARCH_TEMPLATES.get(source, [])])

    for isbn in variants:
        if deadline_expired(deadline):
            break

        for search_template in search_templates:
            if deadline_expired(deadline):
                break
            search_url = search_template.format(isbn=isbn)
            final_url, html = http_get_html(search_url, deadline=deadline)
            if not html:
                continue

            record = extract_book_from_html(html, variants, source, final_url)
            if record:
                records.append(record)
                break

            soup = BeautifulSoup(html, "html.parser")
            product_links = []
            if "kitapsec" in canonical_key(source):
                product_links = kitapsec_product_links(soup, final_url, variants)
            if not product_links:
                product_links = candidate_product_links(soup, final_url, variants)
            for link in product_links[:link_limit]:
                if deadline_expired(deadline):
                    break
                if link in seen_links:
                    continue
                seen_links.add(link)
                product_url, product_html = http_get_html(link, deadline=deadline)
                if not product_html:
                    continue
                record = extract_book_from_html(product_html, variants, source, product_url)
                if record:
                    records.append(record)
                    return records
            if records:
                break
        if records:
            break
    return records


def find_prices_specific_retailer(variants: list[str], source: str, template: str, deadline: float | None = None, link_limit: int = 8) -> list[tuple[float, str]]:
    seen_links = set()
    search_templates = unique_keep_order([template, *SITE_EXTRA_SEARCH_TEMPLATES.get(source, [])])
    for isbn in variants:
        if deadline_expired(deadline):
            break
        for search_template in search_templates:
            if deadline_expired(deadline):
                break
            final_url, html = http_get_html(search_template.format(isbn=isbn), deadline=deadline)
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            title_hint = clean_text(soup.title.get_text(" ") if soup.title else "")
            if variant_in_text(variants, html) and not looks_like_search_page(final_url, title_hint):
                price = extract_price_from_soup(soup, source)
                if price:
                    return [(price, source)]
            product_links = kitapsec_product_links(soup, final_url, variants) if "kitapsec" in canonical_key(source) else []
            if not product_links:
                product_links = candidate_product_links(soup, final_url, variants)
            for link in product_links[:link_limit]:
                if deadline_expired(deadline) or link in seen_links:
                    continue
                seen_links.add(link)
                product_url, product_html = http_get_html(link, deadline=deadline)
                if not product_html or not variant_in_text(variants, product_html):
                    continue
                price = extract_price_from_soup(BeautifulSoup(product_html, "html.parser"), source)
                if price:
                    return [(price, source)]
    return []



def search_turkish_retailers(
    variants: list[str],
    deadline: float | None = None,
    link_limit: int = 10,
) -> list[dict]:
    records = []
    for source, template in TURKISH_RETAILERS:
        if deadline_expired(deadline):
            break
        records.extend(search_specific_retailer(variants, source, template, deadline=deadline, link_limit=link_limit))
    return records


def lookup_specific_retailer(
    raw_isbn: str,
    retailer_key: str,
    max_seconds: int = 20,
    link_limit: int = 12,
) -> dict:
    normalized = normalize_isbn(raw_isbn)
    if not normalized:
        return {
            "ok": False,
            "error": "Geçerli bir ISBN bulunamadı.",
            "normalized": None,
            "book": blank_book(only_digits(raw_isbn)),
            "records_count": 0,
        }

    config = SITE_SPECIFIC_RETAILERS.get(retailer_key)
    if not config:
        return {
            "ok": False,
            "error": "Bilinmeyen kitap sitesi.",
            "normalized": normalized,
            "book": blank_book(normalized.get("isbn13") or normalized["variants"][0]),
            "records_count": 0,
        }

    deadline = time.monotonic() + max_seconds if max_seconds else None
    source, template = config
    records = search_specific_retailer(
        normalized["variants"],
        source,
        template,
        deadline=deadline,
        link_limit=link_limit,
    )
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
        "error": f"{source} üzerinde güvenilir kayıt bulunamadı.",
        "normalized": normalized,
        "book": blank_book(normalized.get("isbn13") or normalized["variants"][0]),
        "records_count": len(records),
    }


def lookup_retailer_priority(raw_isbn: str, max_seconds: int = 30, link_limit: int = 12) -> dict:
    normalized = normalize_isbn(raw_isbn)
    if not normalized:
        return {
            "ok": False,
            "error": "Geçerli bir ISBN bulunamadı.",
            "normalized": None,
            "book": blank_book(only_digits(raw_isbn)),
            "records_count": 0,
        }

    deadline = time.monotonic() + max_seconds if max_seconds else None
    records = []
    for source, template in SITE_SPECIFIC_RETAILERS.values():
        if deadline_expired(deadline):
            break
        records.extend(
            search_specific_retailer(
                normalized["variants"],
                source,
                template,
                deadline=deadline,
                link_limit=link_limit,
            )
        )
        merged = merge_records(normalized, records)
        if merged and merged.get("title") and merged.get("author") and merged.get("publisher"):
            break

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
        "error": "Kitapseç ve Kitapyurdu üzerinde güvenilir kayıt bulunamadı.",
        "normalized": normalized,
        "book": blank_book(normalized.get("isbn13") or normalized["variants"][0]),
        "records_count": len(records),
    }


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
            if field == "estimated_price":
                value = record.get(field)
                if value and not merged.get(field):
                    merged[field] = value
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
def fetch_books_from_supabase() -> list[dict]:
    try:
        response = supabase.table("books").select("*").execute()
        return response.data or []
    except Exception as exc:
        st.error(f"Kitaplar yüklenemedi: {exc}")
        return []


@st.cache_data(ttl=60 * 10, show_spinner=False)
def fetch_live_books_overlay() -> list[dict]:
    """Statik paketin üstüne güncel Supabase alanlarını bindirmek için hafif canlı katman."""
    fields = (
        "id,isbn,title,author,publisher,reading_status,current_page,o_da_okudu,"
        "reading_started_at,reading_finished_at,reread_wanted,reading_comment,"
        "category,favorite,tags,notes,loaned_to,estimated_price,estimated_price_source,"
        "estimated_price_checked_at,created_at,updated_at"
    )
    page_size = 1000
    start = 0
    rows = []
    try:
        while True:
            response = (
                supabase.table("books")
                .select(fields)
                .range(start, start + page_size - 1)
                .execute()
            )
            batch = response.data or []
            rows.extend(batch)
            if len(batch) < page_size:
                break
            start += page_size
    except Exception:
        return []
    return rows


def merge_static_books_with_live_overlay(static_books: list[dict]) -> list[dict]:
    live_rows = fetch_live_books_overlay()
    if not live_rows:
        return static_books

    live_by_id = {clean_text(row.get("id")): row for row in live_rows if clean_text(row.get("id"))}
    live_by_isbn = {only_digits(row.get("isbn")): row for row in live_rows if only_digits(row.get("isbn"))}
    used_live_ids = set()
    merged = []

    for static_book in static_books:
        item = dict(static_book)
        static_cover_url = clean_text(item.get("cover_url"))
        static_local_cover = clean_text(item.get("local_cover_path"))
        static_remote_cover = clean_text(item.get("remote_cover_url"))

        live = live_by_id.get(clean_text(item.get("id"))) or live_by_isbn.get(only_digits(item.get("isbn")))
        if live:
            item.update(live)
            live_id = clean_text(live.get("id"))
            if live_id:
                used_live_ids.add(live_id)

            # Kapak statik pakette yerelse onu koruruz; canlı alanlar eski statik bilgileri ezebilir.
            if static_cover_url and not static_cover_url.startswith(("http://", "https://", "data:")):
                item["cover_url"] = static_cover_url
                if static_local_cover:
                    item["local_cover_path"] = static_local_cover
                item["remote_cover_url"] = static_remote_cover or clean_text(live.get("cover_url"))
            elif static_local_cover:
                item["local_cover_path"] = static_local_cover

        merged.append(item)

    static_keys = {
        clean_text(book.get("id")) or only_digits(book.get("isbn"))
        for book in static_books
        if clean_text(book.get("id")) or only_digits(book.get("isbn"))
    }
    for live in live_rows:
        live_key = clean_text(live.get("id")) or only_digits(live.get("isbn"))
        if live_key and live_key not in static_keys and clean_text(live.get("id")) not in used_live_ids:
            merged.append(live)

    return merged


def clear_live_library_cache():
    try:
        fetch_live_books_overlay.clear()
    except Exception:
        pass


def fetch_books() -> list[dict]:
    static_books = load_static_library_books()
    if static_books:
        return merge_static_books_with_live_overlay(static_books)
    return fetch_books_from_supabase()



def fetch_book_index() -> list[dict]:
    """Hızlı arama için sadece hafif alanları çeker."""
    static_books = fetch_books() if load_static_library_books() else []
    if static_books:
        return sorted(
            [{"id": book.get("id"), "isbn": clean_text(book.get("isbn")), "title": clean_text(book.get("title")), "author": clean_text(book.get("author"))} for book in static_books],
            key=lambda row: canonical_key(row.get("title")),
        )
    page_size = 1000
    start = 0
    rows = []
    try:
        while True:
            response = (
                supabase.table("books")
                .select("id,isbn,title,author")
                .order("title")
                .range(start, start + page_size - 1)
                .execute()
            )
            batch = response.data or []
            rows.extend(batch)
            if len(batch) < page_size:
                break
            start += page_size
        return rows
    except Exception as exc:
        st.warning(f"Hızlı arama listesi yüklenemedi: {exc}")
        return []

def fetch_book_index_by_isbn(isbn: str) -> list[dict]:
    needle = only_digits(isbn)
    if not needle:
        return []
    try:
        response = (
            supabase.table("books")
            .select("id,isbn,title,author")
            .ilike("isbn", f"%{needle}%")
            .limit(25)
            .execute()
        )
        return response.data or []
    except Exception as exc:
        st.warning(f"ISBN için Supabase araması yapılamadı: {exc}")
        return []


def fetch_book_by_id(book_id) -> dict | None:
    if not book_id:
        return None
    try:
        response = supabase.table("books").select("*").eq("id", book_id).limit(1).execute()
        rows = response.data or []
        return rows[0] if rows else None
    except Exception as exc:
        st.warning(f"Kitap kaydı yüklenemedi: {exc}")
        return None


def fetch_book_by_isbn(isbn: str) -> dict | None:
    isbn = normalize_lookup_isbn(isbn)
    if not isbn:
        return None
    try:
        response = supabase.table("books").select("*").eq("isbn", isbn).limit(1).execute()
        rows = response.data or []
        return rows[0] if rows else None
    except Exception as exc:
        st.warning(f"Kitap kaydı yüklenemedi: {exc}")
        return None


def book_identity_key(book: dict) -> str:
    return clean_text(book.get("id")) or only_digits(book.get("isbn")) or canonical_key(
        f"{book.get('title', '')} {book.get('author', '')}"
    )


def find_static_book_by_identity(book_id: str = "", isbn: str = "") -> dict | None:
    book_id = clean_text(book_id)
    isbn_digits = only_digits(isbn)
    for book in load_static_library_books():
        if book_id and clean_text(book.get("id")) == book_id:
            return book
        if isbn_digits and only_digits(book.get("isbn")) == isbn_digits:
            return book
    return None


def merge_book_detail(static_book: dict | None, live_book: dict | None, fallback_book: dict | None = None) -> dict:
    item = dict(static_book or fallback_book or {})
    static_cover_url = clean_text(item.get("cover_url"))
    static_local_cover = clean_text(item.get("local_cover_path"))
    static_remote_cover = clean_text(item.get("remote_cover_url"))

    if live_book:
        item.update(live_book)
        if static_cover_url and not static_cover_url.startswith(("http://", "https://", "data:")):
            item["cover_url"] = static_cover_url
            if static_local_cover:
                item["local_cover_path"] = static_local_cover
            item["remote_cover_url"] = static_remote_cover or clean_text(live_book.get("cover_url"))
        elif static_local_cover:
            item["local_cover_path"] = static_local_cover
    return item


def fetch_book_detail_for_display(summary_book: dict) -> dict:
    static_book = find_static_book_by_identity(summary_book.get("id"), summary_book.get("isbn"))
    live_book = fetch_book_by_id(summary_book.get("id")) or fetch_book_by_isbn(summary_book.get("isbn"))
    return merge_book_detail(static_book, live_book, fallback_book=summary_book)



def fetch_library_summary() -> dict:
    static_books = fetch_books() if load_static_library_books() else []
    if static_books:
        return {
            "count": len(static_books),
            "recent": sorted(
                static_books,
                key=lambda row: clean_text(row.get("created_at") or row.get("updated_at")),
                reverse=True,
            )[:2],
        }

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


def reading_progress(book: dict) -> dict:
    total_pages = safe_int(only_digits(book.get("page_count")), 0)
    current_page = safe_int(only_digits(book.get("current_page")), 0)
    status = clean_text(book.get("reading_status"))
    if status == "Okundu" and total_pages:
        current_page = max(current_page, total_pages)
    if total_pages and current_page:
        percent = min(100, round((current_page / total_pages) * 100, 1))
    elif status == "Okundu":
        percent = 100
    else:
        percent = 0
    return {
        "current_page": current_page,
        "total_pages": total_pages,
        "percent": percent,
    }


def fetch_dashboard_stats() -> dict:
    rows = fetch_books() if load_static_library_books() else []
    if not rows:
        try:
            response = (
                supabase.table("books")
                .select(
                    "id,isbn,title,author,publisher,reading_status,page_count,current_page,"
                    "estimated_price,reading_finished_at"
                )
                .execute()
            )
            rows = response.data or []
        except Exception:
            rows = []

    total = len(rows)
    read_rows = [row for row in rows if clean_text(row.get("reading_status")) == "Okundu"]
    read_count = len(read_rows)
    current_year = datetime.now().year
    this_year_read_count = 0
    author_counts = {}
    publisher_counts = {}
    unread_pages = 0
    known_value = 0.0
    known_value_count = 0
    missing_price = []
    currently_reading = []
    for row in rows:
        status = clean_text(row.get("reading_status"))
        if status != "Okundu":
            unread_pages += safe_int(only_digits(row.get("page_count")), 0)
        if status == "Okunuyor":
            progress = reading_progress(row)
            currently_reading.append(
                {
                    "id": row.get("id"),
                    "isbn": clean_text(row.get("isbn")),
                    "title": book_title(row),
                    "author": book_author(row),
                    **progress,
                }
            )
        price = row.get("estimated_price")
        if price:
            try:
                known_value += float(price)
                known_value_count += 1
            except Exception:
                pass
        elif clean_text(row.get("isbn")):
            missing_price.append(row)

    for row in read_rows:
        finished_year = first_year(row.get("reading_finished_at"))
        if finished_year and safe_int(finished_year, 0) == current_year:
            this_year_read_count += 1
        for author in [part.strip() for part in clean_text(row.get("author")).split(",") if part.strip()]:
            author_counts[author] = author_counts.get(author, 0) + 1
        publisher = normalize_publisher_name(row.get("publisher"))
        if publisher:
            publisher_counts[publisher] = publisher_counts.get(publisher, 0) + 1

    top_author, top_author_count = max(author_counts.items(), key=lambda item: item[1], default=("", 0))
    top_publisher, top_publisher_count = max(publisher_counts.items(), key=lambda item: item[1], default=("", 0))

    return {
        "total": total,
        "read_count": read_count,
        "this_year_read_count": this_year_read_count,
        "read_percent": round((read_count / total) * 100, 1) if total else 0,
        "unread_pages": unread_pages,
        "known_value": round(known_value, 2),
        "known_value_count": known_value_count,
        "top_author": top_author,
        "top_author_count": top_author_count,
        "top_publisher": top_publisher,
        "top_publisher_count": top_publisher_count,
        "currently_reading": currently_reading,
        "missing_price": missing_price,
    }


def find_price_for_isbn(isbn: str, max_seconds: int = 14) -> tuple[float | None, str]:
    normalized = normalize_isbn(isbn)
    if not normalized:
        return None, ""
    deadline = time.monotonic() + max_seconds
    prices = []
    for retailer_key in ("kitapsec", "kitapyurdu"):
        if deadline_expired(deadline):
            break
        source, template = SITE_SPECIFIC_RETAILERS[retailer_key]
        prices.extend(find_prices_specific_retailer(normalized["variants"], source, template, deadline=deadline, link_limit=8))
    if prices:
        prices = sorted(prices, key=lambda item: float(item[0]))
        return float(prices[0][0]), prices[0][1]
    records = []
    records.extend(search_direct_isbn_pages(normalized["variants"], deadline=deadline))
    if not deadline_expired(deadline):
        records.extend(search_turkish_retailers(normalized["variants"], deadline=deadline, link_limit=4))
    record_prices = [(record.get("estimated_price"), clean_text(record.get("estimated_price_source") or record.get("_source"))) for record in records if record.get("estimated_price")]
    if not record_prices:
        return None, ""
    record_prices = sorted(record_prices, key=lambda item: float(item[0]))
    return float(record_prices[0][0]), record_prices[0][1]


def update_book_estimated_price(book_id, price: float, source: str) -> bool:
    try:
        supabase.table("books").update(
            {
                "estimated_price": price,
                "estimated_price_source": source,
                "estimated_price_checked_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("id", book_id).execute()
        clear_live_library_cache()
        return True
    except Exception as exc:
        show_schema_error(exc)
        return False


def insert_book(data: dict) -> bool:
    payload = normalize_book_payload(data)
    try:
        supabase.table("books").insert(payload).execute()
        clear_live_library_cache()
        return True
    except Exception as exc:
        show_schema_error(exc)
        return False


def update_book(book_id, data: dict) -> bool:
    payload = normalize_book_payload(data)
    try:
        supabase.table("books").update(payload).eq("id", book_id).execute()
        clear_live_library_cache()
        return True
    except Exception as exc:
        show_schema_error(exc)
        return False


def delete_book(book_id) -> bool:
    try:
        supabase.table("books").delete().eq("id", book_id).execute()
        clear_live_library_cache()
        return True
    except Exception as exc:
        st.error(f"Silme işlemi başarısız: {exc}")
        return False


def isbn_exists(isbn: str) -> dict | None:
    isbn = clean_text(isbn)
    if not isbn:
        return None
    static_book = find_static_book_by_isbn(isbn)
    if static_book:
        return {
            "id": static_book.get("id"),
            "title": clean_text(static_book.get("title")),
            "isbn": clean_text(static_book.get("isbn")),
        }
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


def fetch_wishlist_items() -> list[dict]:
    try:
        response = (
            supabase.table("wishlist_items")
            .select("*")
            .order("priority")
            .order("created_at", desc=False)
            .execute()
        )
        return response.data or []
    except Exception:
        return []


def insert_wishlist_item(data: dict) -> bool:
    payload = {
        "isbn": clean_text(data.get("isbn")),
        "title": clean_text(data.get("title")),
        "author": clean_text(data.get("author")),
        "publisher": normalize_publisher_name(data.get("publisher")),
        "cover_url": clean_text(data.get("cover_url")),
        "priority": safe_int(data.get("priority"), 999),
        "note": clean_text(data.get("note")),
    }
    try:
        supabase.table("wishlist_items").insert(payload).execute()
        return True
    except Exception as exc:
        show_schema_error(exc)
        return False


def update_wishlist_order(rows: list[dict]) -> bool:
    try:
        for row in rows:
            item_id = row.get("id")
            if item_id:
                supabase.table("wishlist_items").update(
                    {
                        "priority": safe_int(row.get("priority"), 999),
                        "note": clean_text(row.get("note")),
                    }
                ).eq("id", item_id).execute()
        return True
    except Exception as exc:
        st.error(f"Wishlist sıralaması güncellenemedi: {exc}")
        return False


def delete_wishlist_item(item_id) -> bool:
    try:
        supabase.table("wishlist_items").delete().eq("id", item_id).execute()
        return True
    except Exception as exc:
        st.error(f"Wishlist kaydı silinemedi: {exc}")
        return False


def fetch_recommendation_lists() -> list[dict]:
    try:
        response = (
            supabase.table("recommendation_lists")
            .select("*")
            .order("person_name")
            .execute()
        )
        return response.data or []
    except Exception:
        return []


def fetch_recommendation_items(list_id) -> list[dict]:
    try:
        response = (
            supabase.table("recommendation_items")
            .select("id,book_id,position,note")
            .eq("list_id", list_id)
            .order("position")
            .execute()
        )
        items = response.data or []
        book_ids = [clean_text(item.get("book_id")) for item in items if clean_text(item.get("book_id"))]
        books_by_id = {}
        if book_ids:
            book_response = (
                supabase.table("books")
                .select("id,isbn,title,author,cover_url,publisher,page_count,reading_status")
                .in_("id", book_ids)
                .execute()
            )
            books_by_id = {clean_text(book.get("id")): book for book in (book_response.data or [])}
        for item in items:
            item["books"] = books_by_id.get(clean_text(item.get("book_id")), {})
        return items
    except Exception:
        return []


def create_recommendation_list(person_name: str) -> bool:
    try:
        supabase.table("recommendation_lists").insert({"person_name": clean_text(person_name)}).execute()
        return True
    except Exception as exc:
        show_schema_error(exc)
        return False


def add_recommendation_item(list_id, book_id, position: int) -> bool:
    try:
        supabase.table("recommendation_items").upsert(
            {"list_id": list_id, "book_id": clean_text(book_id), "position": position},
            on_conflict="list_id,book_id",
        ).execute()
        return True
    except Exception as exc:
        show_schema_error(exc)
        return False


def update_recommendation_items(rows: list[dict]) -> bool:
    try:
        for row in rows:
            item_id = row.get("id")
            if item_id:
                supabase.table("recommendation_items").update(
                    {
                        "position": safe_int(row.get("position"), 999),
                        "note": clean_text(row.get("note")),
                    }
                ).eq("id", item_id).execute()
        return True
    except Exception as exc:
        st.error(f"Tavsiye sıralaması güncellenemedi: {exc}")
        return False


def delete_recommendation_item(item_id) -> bool:
    try:
        supabase.table("recommendation_items").delete().eq("id", item_id).execute()
        return True
    except Exception as exc:
        st.error(f"Tavsiye kaydı silinemedi: {exc}")
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
        "current_page",
        "first_print_year",
        "reading_status",
        "o_da_okudu",
        "reading_started_at",
        "reading_finished_at",
        "reread_wanted",
        "reading_comment",
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

def safe_static_filename(value: str, fallback: str = "book") -> str:
    value = canonical_key(value)[:80]
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or fallback


def cover_extension_from_url(url: str, content_type: str = "") -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return suffix
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    return ".jpg"


def build_static_library_zip(books: list[dict]) -> tuple[bytes, dict]:
    buffer = io.BytesIO()
    package_books = []
    downloaded = 0
    failed = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, book in enumerate(books, start=1):
            item = dict(book)
            cover_url = clean_text(item.get("cover_url"))
            item.pop("local_cover_path", None)
            item.pop("remote_cover_url", None)
            if cover_url and cover_url.startswith(("http://", "https://")):
                try:
                    response = requests.get(cover_url, headers=HTTP_HEADERS, timeout=10)
                    if response.status_code == 200 and response.content:
                        stem_source = clean_text(item.get("isbn")) or clean_text(item.get("id")) or clean_text(item.get("title")) or str(index)
                        filename = safe_static_filename(stem_source, fallback=f"book-{index}")
                        extension = cover_extension_from_url(cover_url, response.headers.get("Content-Type", ""))
                        local_cover_path = f"static_library/covers/{filename}{extension}"
                        archive.writestr(local_cover_path, response.content)
                        item["remote_cover_url"] = cover_url
                        item["local_cover_path"] = local_cover_path
                        downloaded += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
            package_books.append(item)
        payload = {"app": APP_NAME, "schema_version": 3, "generated_at": datetime.now(timezone.utc).isoformat(), "book_count": len(package_books), "books": package_books}
        archive.writestr("static_library/books.json", json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        archive.writestr(
            "static_library/README.txt",
            "GitHub web yuklemede 100 dosya sinirina takilirsan ZIP'i acma. "
            "Bu dosyayi static_library.zip adiyla app.py ile ayni seviyeye yukle. "
            "Istersen ZIP'i acip static_library klasorunu de repo kokune koyabilirsin. "
            "Okunma durumu, not, puan gibi degisen alanlar Supabase'den canli alinir; "
            "statik paket kitap bilgisi ve kapak icin hiz katmanidir.\n",
        )
    return buffer.getvalue(), {"books": len(package_books), "covers": downloaded, "cover_failures": failed}




# --- UI YARDIMCILARI ---
def inject_css():
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.4rem;
            max-width: 98vw;
            padding-left: 1.4rem;
            padding-right: 1.4rem;
        }
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


def inject_pwa_head():
    components.html(
        """
        <script>
        (function () {
          const doc = window.parent.document;
          const appName = "Badger's Book App";
          const manifestHref = "app/static/manifest.json";
          const icon192 = "app/static/icons/icon-192.png";
          const icon512 = "app/static/icons/icon-512.png";

          function upsertMeta(name, content, attrName = "name") {
            let el = doc.head.querySelector(`meta[${attrName}="${name}"]`);
            if (!el) {
              el = doc.createElement("meta");
              el.setAttribute(attrName, name);
              doc.head.appendChild(el);
            }
            el.setAttribute("content", content);
          }

          function upsertLink(rel, href, extra = {}) {
            let el = doc.head.querySelector(`link[rel="${rel}"]`);
            if (!el) {
              el = doc.createElement("link");
              el.setAttribute("rel", rel);
              doc.head.appendChild(el);
            }
            el.setAttribute("href", href);
            for (const [key, value] of Object.entries(extra)) {
              el.setAttribute(key, value);
            }
          }

          doc.title = appName;
          upsertLink("manifest", manifestHref);
          upsertLink("icon", icon192, { type: "image/png", sizes: "192x192" });
          upsertLink("apple-touch-icon", icon192, { sizes: "192x192" });
          upsertLink("apple-touch-icon", icon512, { sizes: "512x512" });
          upsertMeta("application-name", appName);
          upsertMeta("apple-mobile-web-app-title", appName);
          upsertMeta("theme-color", "#0e1117");
          upsertMeta("mobile-web-app-capable", "yes");
          upsertMeta("apple-mobile-web-app-capable", "yes");
          upsertMeta("apple-mobile-web-app-status-bar-style", "black-translucent");
          upsertMeta("viewport", "width=device-width, initial-scale=1, viewport-fit=cover");
        })();
        </script>
        """,
        height=0,
        width=0,
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

    current = st.session_state.get("page", "home")
    labels = list(PAGE_LABELS.values())
    reverse_labels = {value: key for key, value in PAGE_LABELS.items()}
    selected_label = st.sidebar.radio(
        "Menü",
        labels,
        index=labels.index(PAGE_LABELS.get(current, PAGE_LABELS["home"])),
    )
    selected_page = reverse_labels[selected_label]
    st.session_state["page"] = selected_page

    if selected_page != "detail_library":
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
    cover_url = resolve_static_cover_path(cover_url)
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
    o_da_okudu = "O ;) da Okudu" if book.get("o_da_okudu") else ""
    pieces = [status, o_da_okudu, favorite, *tags[:4]]
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
        current_page = st.text_input(
            "Şu Anki Sayfa",
            value=clean_text(initial.get("current_page")),
            key=f"{prefix}_current_page",
        )
    with c2:
        reading_status = st.selectbox(
            "Okunma Durumu",
            READING_STATUS_OPTIONS,
            index=status_index(initial.get("reading_status")),
            key=f"{prefix}_status",
        )
        o_da_okudu = st.checkbox(
            "O ;) da Okudu",
            value=bool(initial.get("o_da_okudu")),
            key=f"{prefix}_o_da_okudu",
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

    c5, c6 = st.columns(2)
    with c5:
        reading_started_at = st.text_input(
            "Okumaya Başlama Tarihi",
            value=normalize_date_text(initial.get("reading_started_at")),
            placeholder="YYYY-MM-DD",
            key=f"{prefix}_reading_started_at",
        )
        reread_wanted = st.checkbox(
            "Tekrar okumak ister miyim?",
            value=bool(initial.get("reread_wanted")),
            key=f"{prefix}_reread_wanted",
        )
    with c6:
        reading_finished_at = st.text_input(
            "Bitirme Tarihi",
            value=normalize_date_text(initial.get("reading_finished_at")),
            placeholder="YYYY-MM-DD",
            key=f"{prefix}_reading_finished_at",
        )
        read_days = days_between_dates(reading_started_at, reading_finished_at)
        st.caption(f"Okuma süresi: {read_days} gün" if read_days else "Okuma süresi için başlangıç ve bitiş tarihi gir.")

    notes = st.text_area("Notlar", value=clean_text(initial.get("notes")), key=f"{prefix}_notes")
    reading_comment = st.text_area(
        "Kısa Okuma Yorumu",
        value=clean_text(initial.get("reading_comment")),
        key=f"{prefix}_reading_comment",
    )

    return {
        "isbn": clean_text(initial.get("isbn")),
        "title": title,
        "author": author,
        "translator": translator,
        "publisher": publisher,
        "page_count": page_count,
        "current_page": current_page,
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
        "o_da_okudu": o_da_okudu,
        "reading_started_at": reading_started_at,
        "reading_finished_at": reading_finished_at,
        "reread_wanted": reread_wanted,
        "reading_comment": reading_comment,
        "favorite": favorite,
        "tags": parse_tags(tags),
        "notes": notes,
        "loaned_to": loaned_to,
        "rating": rating or None,
    }


def is_usable_kitapsec_update_value(field: str, value) -> bool:
    value_text = clean_text(value)
    if not value_text:
        return False
    bad_values = {
        "bulunamadi",
        "bulunamadı",
        "yok",
        "none",
        "null",
        "belirtilmemis",
        "belirtilmemiş",
        "-",
    }
    if canonical_key(value_text) in bad_values:
        return False
    if field == "page_count" and not only_digits(value_text):
        return False
    if field == "first_print_year" and not first_year(value_text):
        return False
    return True


def kitapsec_update_payload(current: dict, fetched: dict) -> dict:
    payload = {field: current.get(field) for field in SAVE_FIELDS if field in current}
    payload["isbn"] = clean_text(current.get("isbn")) or clean_text(fetched.get("isbn"))

    for field in BOOK_FIELDS:
        if field == "isbn":
            continue
        if field == "estimated_price":
            value = fetched.get(field)
            if value not in ("", None):
                payload[field] = value
            continue
        value = fetched.get(field)
        if is_usable_kitapsec_update_value(field, value):
            payload[field] = clean_text(value)
    for field in (
        "category",
        "reading_status",
        "o_da_okudu",
        "reading_started_at",
        "reading_finished_at",
        "reread_wanted",
        "reading_comment",
        "favorite",
        "tags",
        "notes",
        "loaned_to",
        "rating",
    ):
        payload[field] = current.get(field)
    return payload


def kitapsec_preview_rows(current: dict, fetched: dict) -> list[dict]:
    fields = [
        ("title", "Kitap Adı"),
        ("author", "Yazar"),
        ("translator", "Çevirmen"),
        ("publisher", "Yayınevi"),
        ("page_count", "Sayfa"),
        ("first_print_year", "Yıl"),
        ("paper_type", "Hamur"),
        ("dimensions", "Ebat"),
        ("language", "Dil"),
        ("genre", "Tür / Konu"),
        ("estimated_price", "Fiyat"),
    ]
    rows = []
    for field, label in fields:
        old_value = current.get(field)
        new_value = fetched.get(field)
        if field == "estimated_price":
            old_text = format_tl(old_value) if old_value else ""
            new_text = format_tl(new_value) if new_value else ""
        else:
            old_text = clean_text(old_value)
            new_text = clean_text(new_value)
        if old_text or new_text:
            rows.append({"Alan": label, "Mevcut Bilgi": old_text, "Kitapseç Bilgisi": new_text})
    return rows


def render_kitapsec_update_controls(book: dict):
    book_id = clean_text(book.get("id")) or normalize_lookup_isbn(book.get("isbn")) or canonical_key(book_title(book))
    isbn = clean_text(book.get("isbn"))
    result_key = f"library_kitapsec_result_{book_id}"
    error_key = f"library_kitapsec_error_{book_id}"
    st.divider()
    st.subheader("Kitapseç Güncelleme")
    fetch_col, clear_col = st.columns([0.72, 0.28])
    with fetch_col:
        if st.button("Kitapseç'ten Güncelle", key=f"kitapsec_fetch_{book_id}", use_container_width=True):
            st.session_state.pop(error_key, None)
            if not isbn:
                st.session_state[error_key] = "Bu kitapta ISBN yok; Kitapseç araması yapılamaz."
            else:
                with st.spinner(f"{isbn} Kitapseç'te aranıyor..."):
                    lookup = lookup_specific_retailer(isbn, "kitapsec", max_seconds=20, link_limit=12)
                if lookup.get("ok") and lookup.get("book", {}).get("title"):
                    st.session_state[result_key] = lookup["book"]
                else:
                    st.session_state.pop(result_key, None)
                    st.session_state[error_key] = lookup.get("error") or "Kitapseç üzerinde güvenilir kayıt bulunamadı."
    with clear_col:
        if st.button("Önizlemeyi Temizle", key=f"kitapsec_clear_{book_id}", use_container_width=True):
            st.session_state.pop(result_key, None)
            st.session_state.pop(error_key, None)
            st.rerun()
    if st.session_state.get(error_key):
        st.warning(st.session_state[error_key])
    fetched = st.session_state.get(result_key)
    if not fetched:
        return
    st.info("Kitapseç bilgileri bulundu. Sadece onay verirsen mevcut kitap güncellenir; boş gelen alanlar eski bilgiyi silmez.")
    preview_col, cover_col = st.columns([0.78, 0.22])
    with preview_col:
        rows = kitapsec_preview_rows(book, fetched)
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)
        if clean_text(fetched.get("source_url")):
            st.markdown(f"[Kitapseç sayfasını aç]({fetched['source_url']})")
    with cover_col:
        render_cover(fetched.get("cover_url"), width=110)
    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        if st.button("Evet, Bu Bilgilerle Güncelle", type="primary", key=f"kitapsec_confirm_{book_id}", use_container_width=True):
            current_book = fetch_book_detail_for_display(book)
            if update_book(current_book.get("id") or book.get("id"), kitapsec_update_payload(current_book, fetched)):
                st.session_state.pop(result_key, None)
                st.session_state.pop(error_key, None)
                st.success("Kitap Kitapseç bilgileriyle güncellendi.")
                st.rerun()
    with cancel_col:
        if st.button("Hayır, Dokunma", key=f"kitapsec_cancel_{book_id}", use_container_width=True):
            st.session_state.pop(result_key, None)
            st.session_state.pop(error_key, None)
            st.info("Güncelleme yapılmadı.")
            st.rerun()


def update_single_book_from_kitapsec(book: dict, max_seconds: int = 18) -> tuple[str, str]:
    isbn = clean_text(book.get("isbn"))
    if not isbn:
        return "skipped", f"{book_title(book)}: ISBN yok"
    lookup = lookup_specific_retailer(isbn, "kitapsec", max_seconds=max_seconds, link_limit=12)
    if not lookup.get("ok") or not lookup.get("book", {}).get("title"):
        return "not_found", f"{isbn}: Kitapseç kaydı bulunamadı"
    current_book = fetch_book_detail_for_display(book)
    payload = kitapsec_update_payload(current_book, lookup["book"])
    if update_book(current_book.get("id") or book.get("id"), payload):
        return "updated", f"{isbn}: {clean_text(payload.get('title')) or book_title(book)}"
    return "error", f"{isbn}: güncelleme kaydedilemedi"


def render_library_kitapsec_bulk_controls(all_books: list[dict], visible_books: list[dict]):
    st.subheader("Toplu Kitapseç Güncelleme")
    st.caption("Kitapseç boş bilgi döndürürse mevcut dolu alanlar korunur; okuma durumu, kategori, not, etiket ve puan değişmez.")
    c1, c2 = st.columns(2)
    with c1:
        if st.button(f"Görünen {len(visible_books)} Kitabı Kitapseç'ten Güncelle", use_container_width=True, disabled=not visible_books, key="kitapsec_bulk_visible"):
            run_library_kitapsec_bulk_update(visible_books, "Görünen kitaplar")
    with c2:
        if st.button(f"Tüm {len(all_books)} Kitabı Kitapseç'ten Güncelle", use_container_width=True, disabled=not all_books, key="kitapsec_bulk_all"):
            run_library_kitapsec_bulk_update(all_books, "Tüm kütüphane")


def run_library_kitapsec_bulk_update(target_books: list[dict], label: str):
    books_with_isbn = [book for book in target_books if clean_text(book.get("isbn"))]
    if not books_with_isbn:
        st.warning("Güncellenecek ISBN'li kitap yok.")
        return
    progress = st.progress(0)
    status_box = st.empty()
    result = {"updated": 0, "not_found": 0, "skipped": 0, "error": 0}
    for index, book in enumerate(books_with_isbn, start=1):
        progress.progress(index / len(books_with_isbn))
        status_box.info(f"{index}/{len(books_with_isbn)} Kitapseç'ten güncelleniyor: {book.get('isbn')}")
        status, _ = update_single_book_from_kitapsec(book, max_seconds=18)
        result[status] = result.get(status, 0) + 1
    status_box.success(f"{label} tamamlandı. Güncellenen: {result['updated']} · Bulunamayan: {result['not_found']} · Hata: {result['error']}")



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
        progress = reading_progress(book)
        if progress["current_page"] or progress["percent"]:
            total_text = progress["total_pages"] or "?"
            st.write(f"**Okuma Yüzdesi:** %{progress['percent']} ({progress['current_page']}/{total_text})")
            if progress["percent"]:
                st.progress(int(progress["percent"]))
    with more_col:
        st.write(f"**Çevirmen:** {book.get('translator') or '-'}")
        st.write(f"**Kategori:** {book.get('category') or 'Kategorisiz'}")
        st.write(f"**O ;) da Okudu:** {'Evet' if book.get('o_da_okudu') else 'Hayır'}")
        st.write(f"**Puan:** {book.get('rating') or '-'}")
        st.write(f"**Ödünç:** {book.get('loaned_to') or '-'}")
        if clean_text(book.get("notes")):
            st.write(f"**Not:** {book.get('notes')}")
    st.write(
        f"**Okuma Geçmişi:** "
        f"{normalize_date_text(book.get('reading_started_at')) or '-'} → "
        f"{normalize_date_text(book.get('reading_finished_at')) or '-'}"
    )
    read_days = days_between_dates(book.get("reading_started_at"), book.get("reading_finished_at"))
    st.write(f"**Kaç günde okundu:** {read_days if read_days else '-'}")
    st.write(f"**Tekrar okumak ister miyim?:** {'Evet' if book.get('reread_wanted') else 'Hayır'}")
    if clean_text(book.get("reading_comment")):
        st.write(f"**Kısa yorum:** {book.get('reading_comment')}")
    render_quick_reading_controls(book)
    render_kitapsec_update_controls(book)


def render_quick_reading_controls(book: dict):
    st.divider()
    st.subheader("Hızlı Durum Değiştir")
    book_id = book.get("id")
    if not book_id:
        st.caption("Bu kitabın veritabanı ID bilgisi olmadığı için hızlı güncelleme yapılamıyor.")
        return

    cols = st.columns(5)
    today = datetime.now().date().isoformat()
    for index, status in enumerate(["Okunacak", "Okunuyor", "Okundu", "Yarım Bırakıldı"]):
        with cols[index]:
            if st.button(status, use_container_width=True, key=f"quick_status_{book_id}_{status}"):
                data = dict(book)
                data["reading_status"] = status
                if status == "Okunuyor" and not normalize_date_text(data.get("reading_started_at")):
                    data["reading_started_at"] = today
                if status == "Okundu" and not normalize_date_text(data.get("reading_finished_at")):
                    data["reading_finished_at"] = today
                if status == "Okundu" and clean_text(data.get("page_count")):
                    data["current_page"] = only_digits(data.get("page_count")) or clean_text(data.get("page_count"))
                if update_book(book_id, data):
                    st.success(f"Durum güncellendi: {status}")
                    st.rerun()

    with cols[4]:
        new_value = not bool(book.get("o_da_okudu"))
        label = "O ;) da Okudu" if new_value else "O ;) Okumadı"
        if st.button(label, use_container_width=True, key=f"quick_o_da_okudu_{book_id}"):
            data = dict(book)
            data["o_da_okudu"] = new_value
            if update_book(book_id, data):
                st.success("O ;) da Okudu bilgisi güncellendi.")
                st.rerun()


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


def render_static_library_package_controls():
    with st.expander("GitHub Hız Paketi", expanded=False):
        st.caption(static_library_status_text())
        st.write(
            "Supabase'teki güncel kitapları ve kapakları ZIP olarak hazırlar. "
            "GitHub 100 dosya sınırına takılırsa ZIP'i açma; "
            "static_library.zip adıyla app.py ile aynı seviyeye yükle. "
            "Uygulama bu ZIP'i doğrudan okur."
        )
        if st.button("Supabase'ten GitHub Hız Paketi Oluştur", use_container_width=True, key="build_static_library_zip"):
            with st.spinner("Supabase kitapları ve kapaklar paketleniyor..."):
                books = fetch_books_from_supabase()
                zip_bytes, stats = build_static_library_zip(books)
            st.session_state["static_library_zip_bytes"] = zip_bytes
            st.session_state["static_library_zip_stats"] = stats

        if st.session_state.get("static_library_zip_bytes"):
            stats = st.session_state.get("static_library_zip_stats", {})
            st.success(
                f"Paket hazır: {stats.get('books', 0)} kitap, "
                f"{stats.get('covers', 0)} kapak indirildi, "
                f"{stats.get('cover_failures', 0)} kapak indirilemedi."
            )
            st.download_button(
                "GitHub Hız Paketini İndir (ZIP)",
                data=st.session_state["static_library_zip_bytes"],
                file_name="static_library.zip",
                mime="application/zip",
                use_container_width=True,
            )


def mark_book_row_as_read(row: dict) -> tuple[str, str]:
    isbn = clean_text(row.get("ISBN"))
    current = fetch_book_by_id(row.get("id")) or fetch_book_by_isbn(isbn)
    label = isbn or clean_text(row.get("Kitap Adı")) or "Kitap"
    if not current:
        return "error", f"{label}: veritabanı kaydı yüklenemedi"

    data = dict(current)
    data["reading_status"] = "Okundu"
    if not normalize_date_text(data.get("reading_finished_at")):
        data["reading_finished_at"] = datetime.now().date().isoformat()
    if clean_text(data.get("page_count")):
        data["current_page"] = only_digits(data.get("page_count")) or clean_text(data.get("page_count"))

    if update_book(current.get("id"), data):
        return "updated", f"{label}: Okundu yapıldı"
    return "error", f"{label}: güncelleme kaydedilemedi"


def render_quick_search_page(book_index: list[dict]):
    st.header("Hızlı Arama")
    render_static_library_package_controls()
    st.caption("Bu ekran sadece kitap adı, yazar ve barkod bilgisini kullanır; detaylı Kütüphanem ekranından çok daha hafif çalışır.")

    st.metric("Hızlı listede kayıtlı kitap", len(book_index))
    search_term = st.text_input(
        "Kitap adı, yazar veya barkod yaz",
        placeholder="Örn: Kürk Mantolu Madonna veya 978...",
        key="quick_library_search",
    )

    supabase_fallback_note = ""
    if not search_term:
        st.info("Arama kutusu boşken tüm hızlı indeks gösteriliyor.")
        preview = book_index
    else:
        needle = canonical_key(search_term)
        digit_needle = only_digits(search_term)
        preview = []
        for book in book_index:
            haystack = canonical_key(
                " ".join(
                    [
                        clean_text(book.get("title")),
                        clean_text(book.get("author")),
                        clean_text(book.get("isbn")),
                    ]
                )
            )
            isbn_digits = only_digits(book.get("isbn"))
            if needle in haystack or (digit_needle and digit_needle in isbn_digits):
                preview.append(book)
        if not preview and digit_needle and load_static_library_books():
            preview = fetch_book_index_by_isbn(digit_needle)
            if preview:
                supabase_fallback_note = "Bu ISBN GitHub hız paketinde yoktu; sadece bu arama için Supabase'den getirildi."

    if not preview:
        st.warning("Bu aramada kütüphanede kayıtlı kitap görünmüyor.")
        return
    if supabase_fallback_note:
        st.info(supabase_fallback_note)

    st.session_state.setdefault("quick_library_editor_nonce", 0)
    current_keys = [book_identity_key(book) for book in preview]
    selected_keys = [
        key
        for key in st.session_state.get("quick_library_selected_keys", [])
        if key in set(current_keys)
    ]
    select_col, clear_col = st.columns([0.24, 0.76])
    with select_col:
        if st.button("Gösterilenleri Seç", use_container_width=True, key="quick_library_select_all"):
            st.session_state["quick_library_selected_keys"] = current_keys
            st.session_state["quick_library_editor_nonce"] += 1
            st.rerun()
    with clear_col:
        if st.button("Seçimi Temizle", use_container_width=True, key="quick_library_clear_selection"):
            st.session_state["quick_library_selected_keys"] = []
            st.session_state["quick_library_editor_nonce"] += 1
            st.rerun()

    selected_key_set = set(selected_keys)
    rows = [
        {
            "Seç": book_identity_key(book) in selected_key_set,
            "ISBN": clean_text(book.get("isbn")),
            "Kitap Adı": clean_text(book.get("title")),
            "Yazar": clean_text(book.get("author")),
            "id": book.get("id"),
            "_key": book_identity_key(book),
        }
        for book in preview
    ]
    edited = st.data_editor(
        rows,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key=f"quick_library_editor_{st.session_state['quick_library_editor_nonce']}",
        column_config={
            "Seç": st.column_config.CheckboxColumn("Seç"),
            "ISBN": st.column_config.TextColumn("ISBN", disabled=True),
            "Kitap Adı": st.column_config.TextColumn("Kitap Adı", disabled=True),
            "Yazar": st.column_config.TextColumn("Yazar", disabled=True),
            "id": None,
            "_key": None,
        },
    )
    edited_rows = editor_rows_to_list(edited)
    selected_rows = [row for row in edited_rows if row.get("Seç")]
    st.session_state["quick_library_selected_keys"] = [
        clean_text(row.get("_key")) for row in selected_rows if clean_text(row.get("_key"))
    ]
    st.caption(f"Gösterilen kayıt: {len(preview)} · Seçili: {len(selected_rows)}")

    kitapsec_col, read_col = st.columns(2)
    with kitapsec_col:
        run_kitapsec_update = st.button(
            f"Seçili {len(selected_rows)} Kitabı Kitapseç'ten Güncelle",
            type="primary",
            use_container_width=True,
            disabled=not selected_rows,
            key="quick_library_kitapsec_update",
        )
    with read_col:
        mark_selected_read = st.button(
            f"Seçili {len(selected_rows)} Kitabı Okundu Yap",
            use_container_width=True,
            disabled=not selected_rows,
            key="quick_library_mark_read",
        )

    if run_kitapsec_update:
        progress = st.progress(0)
        status_box = st.empty()
        result = {"updated": 0, "not_found": 0, "skipped": 0, "error": 0}
        examples = []

        for index, row in enumerate(selected_rows, start=1):
            progress.progress(index / len(selected_rows))
            isbn = clean_text(row.get("ISBN"))
            status_box.info(f"{index}/{len(selected_rows)} Kitapseç'ten güncelleniyor: {isbn}")
            current = fetch_book_by_id(row.get("id")) or fetch_book_by_isbn(isbn)
            if not current:
                status = "error"
                message = f"{isbn or row.get('Kitap Adı')}: veritabanı kaydı yüklenemedi"
            else:
                status, message = update_single_book_from_kitapsec(current, max_seconds=18)
            result[status] = result.get(status, 0) + 1
            if len(examples) < 20:
                examples.append(message)

        status_box.success(
            f"Kitapseç güncellemesi tamamlandı. "
            f"Güncellenen: {result['updated']} · "
            f"Bulunamayan: {result['not_found']} · "
            f"Atlanan: {result['skipped']} · "
            f"Hata: {result['error']}"
        )
        with st.expander("İşlem özeti", expanded=False):
            for item in examples:
                st.write(f"- {item}")

    if mark_selected_read:
        progress = st.progress(0)
        status_box = st.empty()
        result = {"updated": 0, "error": 0}
        examples = []

        for index, row in enumerate(selected_rows, start=1):
            progress.progress(index / len(selected_rows))
            isbn = clean_text(row.get("ISBN"))
            status_box.info(f"{index}/{len(selected_rows)} Okundu yapılıyor: {isbn or row.get('Kitap Adı')}")
            status, message = mark_book_row_as_read(row)
            result[status] = result.get(status, 0) + 1
            if len(examples) < 20:
                examples.append(message)

        status_box.success(
            f"Okundu güncellemesi tamamlandı. "
            f"Güncellenen: {result['updated']} · "
            f"Hata: {result['error']}"
        )
        st.session_state["quick_library_selected_keys"] = []
        st.session_state["quick_library_editor_nonce"] += 1
        with st.expander("İşlem özeti", expanded=False):
            for item in examples:
                st.write(f"- {item}")


def lookup_book_for_home_barcode(isbn: str) -> tuple[dict, bool]:
    normalized_isbn = normalize_lookup_isbn(isbn)
    book = blank_book(normalized_isbn)
    if not normalized_isbn:
        return book, False
    lookup = get_book_info_comprehensive(
        normalized_isbn,
        max_seconds=20,
        web_result_limit=4,
        retailer_link_limit=6,
    )
    if lookup.get("ok") and lookup.get("book", {}).get("title"):
        book.update(lookup["book"])
        book["isbn"] = normalized_isbn
        return book, True
    return book, False


def ensure_home_barcode_book_in_library(isbn: str) -> dict | None:
    normalized_isbn = normalize_lookup_isbn(isbn)
    existing = isbn_exists(normalized_isbn)
    if existing:
        return existing

    book, found = lookup_book_for_home_barcode(normalized_isbn)
    if not found or not clean_text(book.get("title")):
        save_pending_isbn(normalized_isbn, note="Ana ekran barkod okutma sırasında bulunamadı", source="home_barcode")
        return None

    if insert_book(book):
        return isbn_exists(normalized_isbn)
    return None


def render_currently_reading_widget(currently_reading: list[dict]):
    st.subheader("Şu An Ne Okuyorum?")
    if not currently_reading:
        st.info("Okunuyor durumunda kitap görünmüyor.")
        return

    for book in currently_reading[:8]:
        title = clean_text(book.get("title")) or "İsimsiz Kitap"
        author = clean_text(book.get("author")) or "Yazar Bilinmiyor"
        current_page = safe_int(book.get("current_page"), 0)
        total_pages = safe_int(book.get("total_pages"), 0)
        percent = float(book.get("percent") or 0)
        st.write(f"**{title}** - {author}")
        if total_pages:
            st.caption(f"{current_page}/{total_pages} sayfa · %{percent}")
        else:
            st.caption("Toplam sayfa bilgisi yok; yüzde hesaplanamadı.")
        st.progress(min(100, int(percent)))


def render_home_barcode_panel():
    st.subheader("Kitap Barkodu Okut")
    if st.button("Barkod Okutma Panelini Aç / Kapat", use_container_width=True, key="home_barcode_toggle"):
        st.session_state["home_barcode_panel_open"] = not st.session_state.get("home_barcode_panel_open", False)
        st.rerun()

    if not st.session_state.get("home_barcode_panel_open", False):
        return

    with st.expander("Barkod Okut ve Nereye Ekleneceğini Seç", expanded=True):
        camera_col, upload_col, manual_col = st.columns(3)
        with camera_col:
            camera_file = st.camera_input("Kamerayla okut", key="home_barcode_camera")
        with upload_col:
            upload_file = st.file_uploader(
                "Barkod fotoğrafı yükle",
                type=["png", "jpg", "jpeg"],
                key="home_barcode_upload",
            )
        with manual_col:
            manual_isbn = st.text_input("ISBN elle gir", key="home_barcode_manual_isbn")

        decoded = decode_isbn_from_image(camera_file) or decode_isbn_from_image(upload_file)
        target_isbn = normalize_lookup_isbn(manual_isbn or decoded)
        if decoded:
            st.success(f"Barkod okundu: {normalize_lookup_isbn(decoded)}")
        if not target_isbn:
            st.caption("Barkod okutunca veya ISBN yazınca ekleme seçenekleri burada açılır.")
            return

        st.write(f"**Seçili ISBN:** {target_isbn}")
        destination = st.selectbox(
            "Bu kitap nereye eklensin?",
            [
                "Kütüphaneye Ekle",
                "Wishlist'e Ekle",
                "Sonra Aranacaklara Ekle",
                "Tavsiye Listesine Ekle",
            ],
            key="home_barcode_destination",
        )

        selected_recommendation = None
        if destination == "Tavsiye Listesine Ekle":
            lists = fetch_recommendation_lists()
            if lists:
                selected_recommendation = st.selectbox(
                    "Tavsiye listesi",
                    lists,
                    format_func=lambda row: row.get("person_name", "İsimsiz"),
                    key="home_barcode_recommendation_list",
                )
            else:
                st.warning("Tavsiye listesi yok. Önce Tavsiyeler sayfasında bir liste oluştur.")

        if not st.button("Seçilen Yere Ekle", type="primary", use_container_width=True, key="home_barcode_add"):
            return

        if destination == "Sonra Aranacaklara Ekle":
            if save_pending_isbn(target_isbn, source="home_barcode"):
                st.success("ISBN Sonra Aranacaklar listesine eklendi.")
            return

        if destination == "Kütüphaneye Ekle":
            if isbn_exists(target_isbn):
                st.warning("Bu ISBN zaten kütüphanede kayıtlı.")
                return
            with st.spinner("Kitap bilgileri aranıyor..."):
                book, found = lookup_book_for_home_barcode(target_isbn)
            if found and insert_book(book):
                st.success(f"Kütüphaneye eklendi: {book_title(book)}")
            else:
                save_pending_isbn(target_isbn, note="Ana ekrandan kütüphaneye eklenirken bulunamadı", source="home_barcode")
                st.warning("Kitap bilgisi bulunamadı; ISBN Sonra Aranacaklar listesine kaydedildi.")
            return

        if destination == "Wishlist'e Ekle":
            with st.spinner("Kitap bilgileri aranıyor..."):
                book, found = lookup_book_for_home_barcode(target_isbn)
            if not found:
                book["title"] = f"ISBN {target_isbn}"
            book["priority"] = len(fetch_wishlist_items()) + 1
            book["note"] = "Ana ekrandan barkodla eklendi"
            if insert_wishlist_item(book):
                st.success("Wishlist'e eklendi.")
            return

        if destination == "Tavsiye Listesine Ekle":
            if not selected_recommendation:
                st.warning("Tavsiye listesi seçmeden ekleyemem.")
                return
            with st.spinner("Kitap kütüphanede hazırlanıyor..."):
                library_book = ensure_home_barcode_book_in_library(target_isbn)
            if not library_book:
                st.warning("Kitap bilgisi bulunamadı; ISBN Sonra Aranacaklar listesine kaydedildi.")
                return
            position = len(fetch_recommendation_items(selected_recommendation.get("id"))) + 1
            if add_recommendation_item(selected_recommendation.get("id"), library_book.get("id"), position):
                st.success("Kitap tavsiye listesine eklendi.")


def render_home_page():
    st.header("Ana Ekran")
    stats = fetch_dashboard_stats()
    total = stats["total"]
    read_count = stats["read_count"]
    unread_pages = stats["unread_pages"]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Toplam Kitap", total)
    col2.metric("Okunan Kitap", read_count)
    col3.metric("Bu Yıl Okunan", stats["this_year_read_count"])
    col4.metric("Kalan Sayfa", unread_pages)

    col5, col6, col7, col8 = st.columns(4)
    col5.metric("Okuma Oranı", f"%{stats['read_percent']}")
    col6.metric("En Çok Okunan Yazar", stats["top_author"] or "-", f"{stats['top_author_count']} kitap" if stats["top_author_count"] else None)
    col7.metric("En Çok Okunan Yayınevi", stats["top_publisher"] or "-", f"{stats['top_publisher_count']} kitap" if stats["top_publisher_count"] else None)
    col8.metric("Tahmini Değer", format_tl(stats["known_value"]))

    nav_col1, nav_col2, nav_col3 = st.columns(3)
    with nav_col1:
        if st.button("Detaylı Kütüphanem", type="primary", use_container_width=True, key="home_go_detail_library"):
            set_page("detail_library")
            st.rerun()
    with nav_col2:
        if st.button("Yeni Kitap Ekle", use_container_width=True, key="home_go_add_book"):
            set_page("add")
            st.rerun()
    with nav_col3:
        if st.button("Eksik Bilgiler", use_container_width=True, key="home_go_missing_info"):
            set_page("missing_info")
            st.rerun()

    st.subheader("Okuma Panosu")
    board_col1, board_col2 = st.columns([0.58, 0.42])
    with board_col1:
        st.write("**Genel okuma ilerlemesi**")
        st.progress(min(100, int(stats["read_percent"])))
        st.caption(f"{read_count}/{total} kitap okundu.")
    with board_col2:
        st.write("**Kütüphane değeri**")
        st.metric("Fiyatı bilinen kitap", stats["known_value_count"])
        st.caption(format_tl(stats["known_value"]))

    render_currently_reading_widget(stats.get("currently_reading", []))
    render_home_barcode_panel()

    st.subheader("Kalan Kitaplar Ne Zaman Biter?")
    daily_pages = st.number_input("Günde kaç sayfa okuyacaksın?", min_value=1, max_value=2000, value=100, step=10)
    days = (unread_pages + daily_pages - 1) // daily_pages if daily_pages else 0
    st.info(f"Günde {daily_pages} sayfa okursan kalan sayfalar yaklaşık {days} günde biter.")

    st.subheader("Tahmini Kütüphane Değeri")
    known_value_text = format_tl(stats["known_value"])
    st.write(f"Şu anda fiyatı bilinen {stats['known_value_count']} kitap için tahmini değer: **{known_value_text}**")
    scan_limit = st.number_input(
        "Bu sefer en fazla kaç eksik fiyat taransın?",
        min_value=1,
        max_value=500,
        value=min(30, max(1, len(stats["missing_price"]))),
        step=5,
    )
    if st.button("Tahmini kütüphane değerini belirle", type="primary", use_container_width=True):
        missing = stats["missing_price"][:scan_limit]
        if not missing:
            st.success("Fiyatı eksik kitap görünmüyor.")
            return
        progress = st.progress(0)
        found_value = 0.0
        found_count = 0
        for index, book in enumerate(missing, start=1):
            progress.progress(index / len(missing))
            price, source = find_price_for_isbn(book.get("isbn"), max_seconds=12)
            if price and update_book_estimated_price(book["id"], price, source):
                found_value += price
                found_count += 1
        st.success(f"{found_count} kitap için fiyat bulundu. Bu tur bulunan değer: {format_tl(found_value)}")
        st.info("Toplam değerin güncel halini görmek için sayfayı yenileyebilirsin.")


def render_wishlist_page():
    st.header("Wishlist")
    st.caption("Satın almak veya edinmek istediğin kitapları barkodla ya da elle ekleyebilirsin.")

    with st.expander("Wishlist'e Kitap Ekle", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            img_file = st.camera_input("Barkod okut", key="wishlist_camera")
            upload = st.file_uploader("Barkod fotoğrafı yükle", type=["png", "jpg", "jpeg"], key="wishlist_upload")
        with c2:
            isbn_input = st.text_input("ISBN", key="wishlist_isbn")
            manual_title = st.text_input("Manuel kitap adı", key="wishlist_manual_title")
            manual_author = st.text_input("Manuel yazar", key="wishlist_manual_author")
            note = st.text_input("Not", key="wishlist_note")

        isbn = isbn_input.strip() if isbn_input else ""
        decoded = decode_isbn_from_image(img_file) or decode_isbn_from_image(upload)
        if decoded:
            isbn = decoded
            st.success(f"Barkod okundu: {isbn}")

        if st.button("Wishlist'e Ekle", use_container_width=True):
            book = blank_book(normalize_lookup_isbn(isbn))
            if isbn:
                lookup = get_book_info_comprehensive(isbn, max_seconds=20, web_result_limit=4, retailer_link_limit=4)
                if lookup.get("book"):
                    book.update(lookup["book"])
            if manual_title:
                book["title"] = manual_title
            if manual_author:
                book["author"] = manual_author
            book["note"] = note
            current_count = len(fetch_wishlist_items())
            book["priority"] = current_count + 1
            if not clean_text(book.get("title")):
                st.error("Wishlist için kitap adı ya da bulunabilir ISBN gerekli.")
            elif insert_wishlist_item(book):
                st.success("Wishlist'e eklendi.")
                st.rerun()

    items = fetch_wishlist_items()
    if not items:
        st.info("Wishlist boş.")
        return

    rows = [
        {
            "id": item.get("id"),
            "priority": item.get("priority") or index,
            "title": item.get("title"),
            "author": item.get("author"),
            "isbn": item.get("isbn"),
            "note": item.get("note"),
        }
        for index, item in enumerate(items, start=1)
    ]
    edited = st.data_editor(
        rows,
        use_container_width=True,
        hide_index=True,
        column_config={
            "id": None,
            "priority": st.column_config.NumberColumn("Sıra", min_value=1, step=1),
            "title": st.column_config.TextColumn("Kitap"),
            "author": st.column_config.TextColumn("Yazar"),
            "isbn": st.column_config.TextColumn("ISBN"),
            "note": st.column_config.TextColumn("Not"),
        },
        key="wishlist_editor",
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Wishlist sırasını kaydet", use_container_width=True):
            if update_wishlist_order(editor_rows_to_list(edited)):
                st.success("Wishlist güncellendi.")
                st.rerun()
    with c2:
        delete_id = st.selectbox("Silinecek kayıt", [""] + [f"{item['id']} · {item['title']}" for item in items])
        if st.button("Seçili wishlist kaydını sil", use_container_width=True) and delete_id:
            if delete_wishlist_item(delete_id.split(" · ")[0]):
                st.success("Wishlist kaydı silindi.")
                st.rerun()


def render_recommendations_page(book_index: list[dict]):
    st.header("Tavsiyeler")
    st.caption("Kişiye özel okuma tavsiye listeleri oluştur.")

    with st.form("create_recommendation_list"):
        person_name = st.text_input("Kime tavsiye edeceksin?")
        create_submit = st.form_submit_button("Yeni Tavsiye Listesi Oluştur")
    if create_submit and person_name:
        if create_recommendation_list(person_name):
            st.success("Tavsiye listesi oluşturuldu.")
            st.rerun()

    lists = fetch_recommendation_lists()
    if not lists:
        st.info("Henüz tavsiye listesi yok.")
        return

    selected = st.selectbox("Tavsiye listesi", lists, format_func=lambda row: row.get("person_name", "İsimsiz"))
    list_id = selected.get("id")
    labels = {
        f"{book.get('title')} - {book.get('author', '')} [{book.get('isbn', '')}]": book
        for book in book_index
        if clean_text(book.get("title"))
    }
    picks = st.multiselect("Bu listeye kitap ekle", list(labels.keys()))
    if st.button("Seçili kitapları tavsiye listesine ekle", use_container_width=True):
        current_count = len(fetch_recommendation_items(list_id))
        added = 0
        for offset, label in enumerate(picks, start=1):
            if add_recommendation_item(list_id, labels[label]["id"], current_count + offset):
                added += 1
        st.success(f"{added} kitap tavsiye listesine eklendi.")
        st.rerun()

    items = fetch_recommendation_items(list_id)
    if not items:
        st.info("Bu listede henüz kitap yok.")
        return

    rows = []
    for item in items:
        book = item.get("books") or {}
        rows.append(
            {
                "id": item.get("id"),
                "position": item.get("position") or 999,
                "title": book.get("title"),
                "author": book.get("author"),
                "note": item.get("note"),
            }
        )
    edited = st.data_editor(
        rows,
        use_container_width=True,
        hide_index=True,
        key="recommendation_items_editor",
        column_config={
            "id": None,
            "position": st.column_config.NumberColumn("Okuma Sırası", min_value=1, step=1),
            "title": st.column_config.TextColumn("Kitap", disabled=True),
            "author": st.column_config.TextColumn("Yazar", disabled=True),
            "note": st.column_config.TextColumn("Not"),
        },
    )
    if st.button("Tavsiye sırasını kaydet", use_container_width=True):
        if update_recommendation_items(editor_rows_to_list(edited)):
            st.success("Tavsiye listesi güncellendi.")
            st.rerun()

    st.subheader("Detay")
    for item in items:
        book = item.get("books") or {}
        with st.expander(f"{book.get('title')} - {book.get('author', '')}"):
            if book.get("cover_url"):
                st.image(book["cover_url"], width=120)
            st.write(f"**ISBN:** {book.get('isbn') or '-'}")
            st.write(f"**Yayınevi:** {book.get('publisher') or '-'}")
            st.write(f"**Sayfa:** {book.get('page_count') or '-'}")


def new_game_question(book_index: list[dict], mode: str) -> dict | None:
    candidates = [book for book in book_index if clean_text(book.get("title")) and clean_text(book.get("author"))]
    if len(candidates) < 4:
        return None
    answer_book = random.choice(candidates)
    if mode == "Kitap adından yazarı bul":
        correct = clean_text(answer_book.get("author"))
        pool = unique_keep_order([clean_text(book.get("author")) for book in candidates if clean_text(book.get("author"))])
        prompt = f"'{answer_book.get('title')}' kitabının yazarı kim?"
    else:
        correct = clean_text(answer_book.get("title"))
        pool = unique_keep_order([clean_text(book.get("title")) for book in candidates if clean_text(book.get("title"))])
        prompt = f"{answer_book.get('author')} hangi kitabın yazarı?"
    wrongs = [item for item in pool if item != correct]
    choices = random.sample(wrongs, k=min(3, len(wrongs))) + [correct]
    random.shuffle(choices)
    return {"prompt": prompt, "correct": correct, "choices": choices}


def render_game_page(book_index: list[dict]):
    st.header("Oyun Oyna")
    st.caption("Kitap adı-yazar eşleştirme oyunu.")
    st.session_state.setdefault("game_score", 0)
    st.session_state.setdefault("game_total", 0)
    mode = st.radio("Oyun modu", ["Kitap adından yazarı bul", "Yazardan kitabı bul"], horizontal=True)

    if st.button("Yeni Soru", use_container_width=True):
        st.session_state["game_question"] = new_game_question(book_index, mode)

    question = st.session_state.get("game_question") or new_game_question(book_index, mode)
    st.session_state["game_question"] = question
    if not question:
        st.warning("Oyun için en az 4 kitapta hem kitap adı hem yazar olmalı.")
        return

    st.subheader(question["prompt"])
    answer = st.radio("Cevabın", question["choices"], key=f"game_answer_{st.session_state.get('game_total', 0)}")
    if st.button("Cevabı Kontrol Et", type="primary", use_container_width=True):
        st.session_state["game_total"] += 1
        if answer == question["correct"]:
            st.session_state["game_score"] += 1
            st.success("Doğru!")
        else:
            st.error(f"Yanlış. Doğru cevap: {question['correct']}")
        st.session_state["game_question"] = new_game_question(book_index, mode)
    st.metric("Puan", f"{st.session_state['game_score']} / {st.session_state['game_total']}")


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
        "queued": [],
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
                if save_pending_isbn(isbn, note="Toplu barkod okutma sırasında bulunamadı", source="bulk_barcode"):
                    result["queued"].append(isbn)

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
            if result.get("queued"):
                st.info(f"{len(result['queued'])} bulunamayan ISBN Sonra Aranacaklar listesine kaydedildi.")
            else:
                st.warning(f"{len(result['unresolved'])} ISBN bulunamadı. Sonra Aranacaklar seçeneğini açarsan kuyruğa kaydedilir.")
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
            result = {"found": [], "unresolved": [], "queued": [], "existing": [], "errors": []}

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
                        if save_pending_isbn(
                            isbn,
                            note="Ultimate toplu barkod aramasında bulunamadı",
                            source="ultimate_bulk",
                        ):
                            result["queued"].append(isbn)

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

            queue_unchecked = st.checkbox(
                "Ekle tikini kaldırdıklarımı Sonra Aranacaklar listesine ekle",
                value=True,
                key="ultimate_queue_unchecked_found",
            )

            if st.button("Seçili Bulunanları Kütüphaneye Ekle", use_container_width=True):
                rows = editor_rows_to_list(edited_found)
                book_by_isbn = {clean_text(book.get("isbn")): book for book in result["found"]}
                inserted = 0
                skipped = 0
                queued_unchecked = 0
                for row in rows:
                    if not row.get("ekle"):
                        if queue_unchecked:
                            isbn = clean_text(row.get("isbn"))
                            if save_pending_isbn(
                                isbn,
                                note="Ultimate listede bulunan bilgi onaylanmadı",
                                source="ultimate_unchecked",
                            ):
                                queued_unchecked += 1
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
                if queued_unchecked:
                    st.info(f"{queued_unchecked} ISBN Sonra Aranacaklar listesine eklendi.")

        if result["unresolved"]:
            if result.get("queued"):
                st.info(f"{len(result['queued'])} bulunamayan ISBN Sonra Aranacaklar listesine kaydedildi.")
            else:
                st.warning(f"{len(result['unresolved'])} ISBN bulunamadı. Sonra Aranacaklar seçeneğini açarsan kuyruğa kaydedilir.")

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


def missing_info_rows(books: list[dict], field: str) -> list[dict]:
    rows = []
    for book in books:
        missing = False
        if field == "estimated_price":
            missing = not book.get("estimated_price")
        elif field == "reading_history":
            missing = clean_text(book.get("reading_status")) == "Okundu" and not normalize_date_text(book.get("reading_finished_at"))
        else:
            missing = not clean_text(book.get(field))
        if missing:
            rows.append(
                {
                    "ISBN": clean_text(book.get("isbn")),
                    "Kitap Adı": book_title(book),
                    "Yazar": book_author(book),
                    "Yayınevi": clean_text(book.get("publisher")),
                    "Durum": clean_text(book.get("reading_status")) or "Okunacak",
                }
            )
    return rows


def render_missing_info_page():
    st.header("Eksik Bilgiler")
    books = fetch_books()
    if not books:
        st.info("Kütüphanede kitap görünmüyor.")
        return

    checks = [
        ("Kapak", "cover_url"),
        ("Yazar", "author"),
        ("Yayınevi", "publisher"),
        ("Sayfa Sayısı", "page_count"),
        ("Yayın / Baskı Yılı", "first_print_year"),
        ("Tahmini Fiyat", "estimated_price"),
        ("Okundu Ama Bitiş Tarihi Yok", "reading_history"),
    ]

    summary_rows = []
    missing_by_label = {}
    for label, field in checks:
        rows = missing_info_rows(books, field)
        missing_by_label[label] = rows
        summary_rows.append({"Eksik Bilgi": label, "Kitap Sayısı": len(rows)})

    st.dataframe(summary_rows, use_container_width=True, hide_index=True)

    selected_label = st.selectbox("Detayını göster", [label for label, _ in checks], key="missing_info_detail")
    rows = missing_by_label.get(selected_label, [])
    if not rows:
        st.success(f"{selected_label} için eksik kayıt görünmüyor.")
        return

    st.caption(f"{selected_label} eksik olan kitap sayısı: {len(rows)}")
    st.dataframe(rows, use_container_width=True, hide_index=True, height=520)

    if selected_label in {"Kapak", "Yazar", "Yayınevi", "Sayfa Sayısı", "Yayın / Baskı Yılı", "Tahmini Fiyat"}:
        st.info("Bu listedeki kitapları Detaylı Kütüphanem sayfasından veya toplu Kitapseç güncellemesiyle tamamlayabilirsin.")


def render_library_page(all_books: list[dict], filters: tuple):
    personal_filter, status_filter, tag_filter, search_term, sort_by = filters
    st.header("Detaylı Kütüphanem")

    inline_search = st.text_input(
        "Listede ara",
        placeholder="Kitap adı, yazar, yayınevi veya ISBN yaz",
        key="detail_library_inline_search",
    )
    active_search_term = inline_search or search_term

    filtered = filter_books(all_books, active_search_term, personal_filter, status_filter, tag_filter)
    books = sort_books(filtered, sort_by)

    total = len(all_books)
    st.caption(f"{len(books)} kitap gösteriliyor · toplam {total} kitap")

    def render_detail_library_footer():
        st.divider()
        render_static_library_package_controls()
        render_library_kitapsec_bulk_controls(all_books, books)

    if not books:
        st.info("Bu filtrelerde kitap bulunamadı.")
        render_detail_library_footer()
        return

    book_by_key = {book_identity_key(book): book for book in books}
    visible_keys = set(book_by_key)
    selected_keys = [
        key
        for key in st.session_state.get("detail_library_selected_keys", [])
        if key in visible_keys
    ]
    selected_key = st.session_state.get("detail_library_selected_key", "")
    if selected_key and selected_key not in visible_keys:
        selected_key = ""
    if selected_key and selected_key not in selected_keys:
        selected_keys.append(selected_key)

    st.session_state["detail_library_selected_keys"] = selected_keys
    if selected_key:
        st.session_state["detail_library_selected_key"] = selected_key
    else:
        st.session_state.pop("detail_library_selected_key", None)

    st.session_state.setdefault("detail_library_editor_nonce", 0)
    action_col1, action_col2, action_col3 = st.columns([0.18, 0.22, 0.60])
    with action_col1:
        if st.button("Tümünü Seç", use_container_width=True, key="detail_library_select_all"):
            st.session_state["detail_library_selected_keys"] = list(book_by_key.keys())
            if books:
                st.session_state["detail_library_selected_key"] = book_identity_key(books[0])
            st.session_state["detail_library_editor_nonce"] += 1
            st.rerun()
    with action_col2:
        if st.button("İşaretli Kutuları Temizle", use_container_width=True, key="detail_library_clear_selection"):
            st.session_state["detail_library_selected_keys"] = []
            st.session_state.pop("detail_library_selected_key", None)
            st.session_state["detail_library_editor_nonce"] += 1
            st.rerun()

    selected_keys_set = set(st.session_state.get("detail_library_selected_keys", []))
    rows = []
    for book in books:
        key = book_identity_key(book)
        rows.append(
            {
                "Detay": key in selected_keys_set,
                "Kitap Adı": book_title(book),
                "Yazar": book_author(book),
                "Yayınevi": clean_text(book.get("publisher")),
                "Durum": clean_text(book.get("reading_status")) or "Okunacak",
                "O ;) da Okudu": "Evet" if book.get("o_da_okudu") else "",
                "ISBN": clean_text(book.get("isbn")),
                "_key": key,
            }
        )

    edited = st.data_editor(
        rows,
        use_container_width=True,
        hide_index=True,
        height=620,
        num_rows="fixed",
        key=f"detail_library_lazy_list_{st.session_state['detail_library_editor_nonce']}",
        column_config={
            "Detay": st.column_config.CheckboxColumn("Detay"),
            "Kitap Adı": st.column_config.TextColumn("Kitap Adı", disabled=True),
            "Yazar": st.column_config.TextColumn("Yazar", disabled=True),
            "Yayınevi": st.column_config.TextColumn("Yayınevi", disabled=True),
            "Durum": st.column_config.TextColumn("Durum", disabled=True),
            "O ;) da Okudu": st.column_config.TextColumn("O ;) da Okudu", disabled=True),
            "ISBN": st.column_config.TextColumn("ISBN", disabled=True),
            "_key": None,
        },
    )

    selected_rows = [row for row in editor_rows_to_list(edited) if row.get("Detay")]
    selected_keys = [clean_text(row.get("_key")) for row in selected_rows if clean_text(row.get("_key"))]
    st.session_state["detail_library_selected_keys"] = selected_keys
    if selected_rows:
        if selected_key not in selected_keys:
            selected_key = selected_keys[-1]
        st.session_state["detail_library_selected_key"] = selected_key
    else:
        selected_key = ""
        st.session_state.pop("detail_library_selected_key", None)

    if not selected_key:
        st.info("Detayını görmek istediğin kitabın satırındaki Detay kutusunu işaretle.")
        render_detail_library_footer()
        return

    if len(selected_keys) > 1:
        selected_label_map = {
            f"{book_title(book_by_key[key])} - {book_author(book_by_key[key])} [{clean_text(book_by_key[key].get('isbn'))}]": key
            for key in selected_keys
            if key in book_by_key
        }
        labels = list(selected_label_map.keys())
        if labels:
            current_label = next((label for label, key in selected_label_map.items() if key == selected_key), labels[0])
            selected_label = st.selectbox(
                "Detayı açılacak kitap",
                labels,
                index=labels.index(current_label),
                key="detail_library_active_detail",
            )
            selected_key = selected_label_map[selected_label]
            st.session_state["detail_library_selected_key"] = selected_key

    selected_book = book_by_key.get(selected_key)
    if not selected_book:
        st.warning("Seçili kitap bu filtrede görünmüyor.")
        render_detail_library_footer()
        return

    st.divider()
    with st.spinner("Kitap detayları çekiliyor..."):
        detail_book = fetch_book_detail_for_display(selected_book)

    st.subheader(book_title(detail_book))
    details_tab, edit_tab = st.tabs(["Bilgiler", "Düzenle"])
    with details_tab:
        render_book_details(detail_book)
    with edit_tab:
        render_book_editor(detail_book)

    render_detail_library_footer()


def render_manual_book_add_section(add_nonce: int):
    with st.expander("Elle Kitap Ekle", expanded=False):
        st.caption("ISBN dahil tüm bilgileri kendin girip internet araması yapmadan doğrudan kütüphaneye kaydedebilirsin.")
        with st.form(f"manual_book_form_{add_nonce}"):
            manual_isbn = st.text_input(
                "ISBN",
                placeholder="9786052361917",
                key=f"manual_book_isbn_{add_nonce}",
            )
            manual_data = build_form_data(
                f"manual_book_{add_nonce}",
                blank_book(normalize_lookup_isbn(manual_isbn) if manual_isbn else ""),
            )
            submitted = st.form_submit_button(
                "Elle Girilen Kitabı Kütüphaneye Kaydet",
                type="primary",
                use_container_width=True,
            )

        if not submitted:
            return

        manual_data["isbn"] = normalize_lookup_isbn(manual_isbn) if manual_isbn else ""
        if not clean_text(manual_data.get("title")):
            st.error("Kitap adı zorunlu.")
            return

        if manual_data.get("isbn"):
            existing = isbn_exists(manual_data.get("isbn"))
            if existing:
                st.warning(f"Bu ISBN zaten kayıtlı: {existing.get('title', 'İsimsiz')}")
                return

        if insert_book(manual_data):
            st.session_state["last_add_success"] = f"'{clean_text(manual_data['title'])}' elle kütüphaneye eklendi."
            set_page("add")
            st.session_state["add_nonce"] = add_nonce + 1
            st.rerun()


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

    render_manual_book_add_section(add_nonce)
    st.divider()

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

    searchable_statuses = {"bekliyor", "bulunamadı", ""}

    def run_pending_batch(mode: str):
        searchable = [item for item in pending_items if clean_text(item.get("status")) in searchable_statuses]
        if not searchable:
            st.info("Aranacak bekleyen ISBN yok.")
            return
        progress = st.progress(0)
        found = 0
        for index, item in enumerate(searchable, start=1):
            isbn = clean_text(item.get("isbn"))
            progress.progress(index / len(searchable))
            if mode in SITE_SPECIFIC_RETAILERS:
                lookup = lookup_specific_retailer(isbn, mode, max_seconds=20, link_limit=12)
            else:
                lookup = lookup_retailer_priority(isbn, max_seconds=20, link_limit=12)
                if not lookup.get("ok"):
                    lookup = get_book_info_comprehensive(
                        isbn,
                        cache_version=LOOKUP_CACHE_VERSION + 2,
                        max_seconds=20,
                        web_result_limit=4,
                        retailer_link_limit=6,
                    )
            if lookup.get("ok") and lookup.get("book", {}).get("title"):
                st.session_state[f"queue_result_{item.get('id')}"] = lookup["book"]
                update_pending_isbn_status(item.get("id"), "bulundu")
                found += 1
            else:
                update_pending_isbn_status(item.get("id"), "bulunamadı")
        st.success(f"Arama tamamlandı. {found} kitap için bilgi bulundu.")
        st.rerun()

    bulk_col1, bulk_col2, bulk_col3 = st.columns(3)
    with bulk_col1:
        if st.button("Tüm Bekleyenleri Kitapseç'te Ara", type="primary", use_container_width=True):
            run_pending_batch("kitapsec")
    with bulk_col2:
        if st.button("Tüm Bekleyenleri Kitapyurdu'nda Ara", use_container_width=True):
            run_pending_batch("kitapyurdu")
    with bulk_col3:
        if st.button("Tüm Bekleyenleri Kitap Sitelerinde Güçlü Ara", use_container_width=True):
            run_pending_batch("all")

    for item in pending_items:
        queue_id = item.get("id")
        isbn = clean_text(item.get("isbn"))
        status = clean_text(item.get("status")) or "bekliyor"
        note = clean_text(item.get("note"))

        with st.expander(f"{isbn} · {status}"):
            st.write(f"**ISBN:** {isbn}")
            if note:
                st.write(f"**Not:** {note}")

            action_col1, action_col2, action_col3, action_col4, action_col5 = st.columns(5)
            with action_col1:
                if st.button("Kitapseç'te Ara", key=f"queue_kitapsec_{queue_id}", use_container_width=True):
                    with st.spinner(f"{isbn} Kitapseç'te aranıyor..."):
                        lookup = lookup_specific_retailer(isbn, "kitapsec", max_seconds=20, link_limit=12)
                    if lookup.get("ok") and lookup.get("book", {}).get("title"):
                        st.session_state[f"queue_result_{queue_id}"] = lookup["book"]
                        update_pending_isbn_status(queue_id, "bulundu")
                        st.success("Kitapseç kaydı bulundu. Aşağıdan kontrol edip ekleyebilirsin.")
                    else:
                        update_pending_isbn_status(queue_id, "bulunamadı")
                        st.warning("Kitapseç üzerinde güvenilir kayıt bulunamadı.")
            with action_col2:
                if st.button("Kitapyurdu'nda Ara", key=f"queue_kitapyurdu_{queue_id}", use_container_width=True):
                    with st.spinner(f"{isbn} Kitapyurdu'nda aranıyor..."):
                        lookup = lookup_specific_retailer(isbn, "kitapyurdu", max_seconds=20, link_limit=12)
                    if lookup.get("ok") and lookup.get("book", {}).get("title"):
                        st.session_state[f"queue_result_{queue_id}"] = lookup["book"]
                        update_pending_isbn_status(queue_id, "bulundu")
                        st.success("Kitapyurdu kaydı bulundu. Aşağıdan kontrol edip ekleyebilirsin.")
                    else:
                        update_pending_isbn_status(queue_id, "bulunamadı")
                        st.warning("Kitapyurdu üzerinde güvenilir kayıt bulunamadı.")
            with action_col3:
                if st.button("Uygulamada Tekrar Ara", key=f"queue_search_{queue_id}", use_container_width=True):
                    with st.spinner(f"{isbn} yeniden aranıyor..."):
                        lookup = get_book_info_comprehensive(isbn, cache_version=LOOKUP_CACHE_VERSION + 1)
                    if lookup.get("ok") and lookup.get("book", {}).get("title"):
                        st.session_state[f"queue_result_{queue_id}"] = lookup["book"]
                        update_pending_isbn_status(queue_id, "bulundu")
                        st.success("Kitap bulundu. Aşağıdan kontrol edip ekleyebilirsin.")
                    else:
                        update_pending_isbn_status(queue_id, "bulunamadı")
                        st.warning("Uygulama güvenilir kayıt bulamadı. İstersen detayları elle ekleyebilirsin.")
            with action_col4:
                if st.button("Detayları Elle Ekle", key=f"queue_manual_{queue_id}", use_container_width=True):
                    st.session_state[f"queue_manual_form_{queue_id}"] = True
                    st.rerun()
            with action_col5:
                if st.button("Listeden Sil", key=f"queue_delete_{queue_id}", use_container_width=True):
                    if delete_pending_isbn(queue_id):
                        st.success("ISBN listeden silindi.")
                        st.rerun()

            if st.session_state.get(f"queue_manual_form_{queue_id}"):
                st.info("ISBN sabit kalacak; diğer kitap bilgilerini elle doldurup doğrudan kütüphaneye ekleyebilirsin.")
                manual_initial = blank_book(normalize_lookup_isbn(isbn))
                close_col, _ = st.columns([0.25, 0.75])
                with close_col:
                    if st.button("Manuel Formu Kapat", key=f"queue_manual_close_{queue_id}", use_container_width=True):
                        st.session_state.pop(f"queue_manual_form_{queue_id}", None)
                        st.rerun()

                with st.form(f"queue_manual_add_form_{queue_id}"):
                    st.write(f"**ISBN:** {normalize_lookup_isbn(isbn)}")
                    data = build_form_data(f"queue_manual_{queue_id}", manual_initial)
                    data["isbn"] = normalize_lookup_isbn(isbn)
                    submitted = st.form_submit_button("Elle Girilen Kitabı Kütüphaneye Ekle", type="primary", use_container_width=True)

                if submitted:
                    if not clean_text(data.get("title")):
                        st.error("Kitap adı zorunlu.")
                    elif isbn_exists(data.get("isbn")):
                        st.warning("Bu ISBN zaten kütüphanede kayıtlı.")
                    elif insert_book(data):
                        update_pending_isbn_status(queue_id, "eklendi")
                        delete_pending_isbn(queue_id)
                        st.session_state.pop(f"queue_manual_form_{queue_id}", None)
                        st.session_state.pop(f"queue_result_{queue_id}", None)
                        st.success("Kitap elle kütüphaneye eklendi ve kuyruktan kaldırıldı.")
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
inject_pwa_head()

if "page" not in st.session_state:
    st.session_state["page"] = "home"
if "add_nonce" not in st.session_state:
    st.session_state["add_nonce"] = 0

filters = render_sidebar()
all_books = filters[5]
book_index = []
if st.session_state.get("page") in {"library", "quick_search", "recommendations", "game"}:
    book_index = fetch_book_index()
render_top_bar()

if st.session_state.get("page") == "home":
    render_home_page()
elif st.session_state.get("page") == "library":
    render_quick_search_page(book_index)
elif st.session_state.get("page") == "add":
    render_add_page()
elif st.session_state.get("page") == "quick_search":
    render_quick_search_page(book_index)
elif st.session_state.get("page") == "detail_library":
    render_library_page(all_books, filters[:5])
elif st.session_state.get("page") == "bulk_add":
    render_bulk_add_page()
elif st.session_state.get("page") == "lookup_queue":
    render_lookup_queue_page()
elif st.session_state.get("page") == "missing_info":
    render_missing_info_page()
elif st.session_state.get("page") == "wishlist":
    render_wishlist_page()
elif st.session_state.get("page") == "recommendations":
    render_recommendations_page(book_index)
elif st.session_state.get("page") == "game":
    render_game_page(book_index)
else:
    render_quick_search_page(book_index)
