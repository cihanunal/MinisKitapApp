import streamlit as st
import requests
from bs4 import BeautifulSoup
from supabase import create_client, Client
from pyzbar.pyzbar import decode
from PIL import Image
import pandas as pd

# --- KONFİGÜRASYON VE BAĞLANTI ---
st.set_page_config(page_title="MinisKitapApp", page_icon="📚", layout="wide")

@st.cache_resource
def init_connection():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

supabase: Client = init_connection()

# --- GELİŞMİŞ KİTAP ARAMA MOTORU ---
def search_dr_com_tr(isbn):
    """D&R web sitesinden ISBN ile kitap bilgilerini kazır."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    search_url = f"https://www.dr.com.tr/search?q={isbn}"
    
    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, "html.parser")
        
        # D&R arama sonucunda direkt ürüne yönlendirebilir veya listede ilk ürünü verebilir
        # Direkt ürün sayfası olduğunu varsayarak özellikleri arayalım:
        book_data = {
            "isbn": isbn,
            "title": "",
            "author": "",
            "publisher": "",
            "translator": "",
            "page_count": "",
            "paper_type": "",
            "dimensions": "",
            "first_print_year": "",
            "print_edition": "",
            "language": "Türkçe"
        }

        # Başlık ve Yazar (Genel HTML yapısına göre)
        title_el = soup.find("h1", class_="product-name")
        if title_el: book_data["title"] = title_el.text.strip()
        
        author_el = soup.find("a", class_="author") # veya benzeri bir class
        if author_el: book_data["author"] = author_el.text.strip()

        # Özellikler listesini (ul.product-property vb.) tarama
        properties = soup.find_all("li")
        for prop in properties:
            text = prop.text.strip()
            if "Çevirmen:" in text: book_data["translator"] = text.replace("Çevirmen:", "").strip()
            elif "Yayınevi:" in text: book_data["publisher"] = text.replace("Yayınevi:", "").strip()
            elif "Sayfa Sayısı:" in text: book_data["page_count"] = text.replace("Sayfa Sayısı:", "").strip()
            elif "Hamur Tipi:" in text: book_data["paper_type"] = text.replace("Hamur Tipi:", "").strip()
            elif "Ebat:" in text: book_data["dimensions"] = text.replace("Ebat:", "").strip()
            elif "İlk Baskı Yılı:" in text: book_data["first_print_year"] = text.replace("İlk Baskı Yılı:", "").strip()
            elif "Baskı Sayısı:" in text: book_data["print_edition"] = text.replace("Baskı Sayısı:", "").strip()

        if book_data["title"]: # Eğer başlık bulabildiysek başarılı say
            return book_data
    except Exception as e:
        print(f"D&R Arama Hatası: {e}")
    return None

def search_google_books(isbn):
    """Google Books API'den temel bilgileri çeker."""
    api_url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
    try:
        response = requests.get(api_url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("totalItems", 0) > 0:
                v = data["items"][0]["volumeInfo"]
                return {
                    "isbn": isbn,
                    "title": v.get("title", ""),
                    "author": ", ".join(v.get("authors", [])),
                    "publisher": v.get("publisher", ""),
                    "published_date": v.get("publishedDate", ""),
                    "genre": ", ".join(v.get("categories", ["Genel"])),
                    "page_count": str(v.get("pageCount", "")),
                    "language": v.get("language", "tr")
                }
    except:
        pass
    return None

def get_book_info_comprehensive(isbn):
    """Farklı kaynakları sırayla tarar ve en dolu veriyi döndürür."""
    # 1. Önce D&R'ı dene (Türkçe kitaplar ve detaylar için en iyisi)
    dr_data = search_dr_com_tr(isbn)
    if dr_data and dr_data["title"]:
        return dr_data
    
    # 2. D&R'da yoksa Google Books'a bak
    google_data = search_google_books(isbn)
    if google_data and google_data["title"]:
        return google_data
        
    return None

# --- KENAR ÇUBUĞU (SIDEBAR) ---
st.sidebar.title("📚 MinisKitapApp")
menu = st.sidebar.radio("Menü", ["🏠 Kütüphanem", "🔍 Kitap Ekle"])

st.sidebar.divider()
st.sidebar.subheader("Kişisel Filtreler")
p_filter = st.sidebar.selectbox("Görünüm", ["Tümü", "Favoriler", "Sıradaki (Okunacak)", "Ödünç Verilenler", "Okuma Durumu"])
st.sidebar.subheader("Kitap Filtreleri")
sort_by = st.sidebar.selectbox("Sıralama", ["İsim (A-Z)", "Yazar (A-Z)", "Yeni Eklenenler"])

# --- ANA SAYFA: KÜTÜPHANEM ---
if menu == "🏠 Kütüphanem":
    st.header("Kitaplığım")
    query = supabase.table("books").select("*")
    
    if sort_by == "İsim (A-Z)": query = query.order("title")
    elif sort_by == "Yazar (A-Z)": query = query.order("author")
    else: query = query.order("created_at", desc=True)

    res = query.execute()
    books = res.data

    if not books:
        st.info("Kütüphanenizde henüz kitap bulunmuyor.")
    else:
        for book in books:
            with st.expander(f"📖 {book.get('title', 'İsimsiz')} - {book.get('author', 'Yazar Bilinmiyor')}"):
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.write(f"**Yayınevi:** {book.get('publisher', '-')}")
                    st.write(f"**Çevirmen:** {book.get('translator', '-')}")
                    st.write(f"**Sayfa Sayısı:** {book.get('page_count', '-')}")
                with col2:
                    st.write(f"**Hamur:** {book.get('paper_type', '-')}")
                    st.write(f"**Baskı Yılı:** {book.get('first_print_year', '-')}")
                    st.write(f"**Kategori:** {book.get('category', '-')}")
                with col3:
                    if st.button("🗑️ Sil", key=f"del_{book['id']}"):
                        supabase.table("books").delete().eq("id", book['id']).execute()
                        st.rerun()

# --- ANA SAYFA: KİTAP EKLE ---
elif menu == "🔍 Kitap Ekle":
    st.header("Yeni Kitap Ekle")
    
    img_file = st.camera_input("Barkodu Okutun")
    isbn_input = st.text_input("Veya ISBN numarasını manuel girin (Örn: 9786052361917)")
    
    target_isbn = None
    
    if img_file:
        img = Image.open(img_file)
        decoded = decode(img)
        if decoded:
            target_isbn = decoded[0].data.decode('utf-8')
            st.success(f"Kameradan Barkod Okundu: {target_isbn}")
            
    if isbn_input:
        target_isbn = isbn_input
        
    if target_isbn:
        with st.spinner("İnternet üzerinde kitap bilgileri aranıyor (D&R, Google Books)..."):
            book_info = get_book_info_comprehensive(target_isbn)
            
        if book_info:
            st.success("Kitap bilgileri başarıyla çekildi!")
            
            with st.form("save_book_form"):
                st.subheader("Kitap Detayları (İsterseniz düzenleyebilirsiniz)")
                
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
                
                if submit and m_title:
                    final_data = {
                        "isbn": target_isbn,
                        "title": m_title,
                        "author": m_author,
                        "translator": m_translator,
                        "publisher": m_publisher,
                        "page_count": m_pages,
                        "paper_type": m_paper,
                        "first_print_year": m_year,
                        "category": m_cat
                    }
                    supabase.table("books").insert(final_data).execute()
                    st.success(f"'{m_title}' kütüphaneye eklendi!")
        else:
            st.warning("Bu barkoda ait kitap bilgisi online kaynaklarda bulunamadı. Lütfen manuel form ile ekleyin.")
