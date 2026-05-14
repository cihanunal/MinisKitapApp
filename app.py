import streamlit as st
import requests
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

# --- YARDIMCI FONKSİYONLAR ---
def get_book_info(isbn):
    """Google Books API kullanarak kitap bilgilerini çeker."""
    api_url = f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}"
    try:
        response = requests.get(api_url)
        if response.status_code == 200:
            data = response.json()
            if data.get("totalItems", 0) > 0:
                v = data["items"][0]["volumeInfo"]
                return {
                    "isbn": isbn,
                    "title": v.get("title", "Bilinmeyen Kitap"),
                    "author": ", ".join(v.get("authors", ["Bilinmeyen Yazar"])),
                    "publisher": v.get("publisher", "Bilinmeyen Yayınevi"),
                    "published_date": v.get("publishedDate", "Bilinmeyen Tarih"),
                    "genre": ", ".join(v.get("categories", ["Genel"])),
                    "description": v.get("description", "")
                }
    except:
        return None
    return None

# --- KENAR ÇUBUĞU (SIDEBAR) - FİLTRELER ---
st.sidebar.title("📚 MinisKitapApp")
menu = st.sidebar.radio("Menü", ["🏠 Kütüphanem", "🔍 Kitap Ekle", "⚙️ Ayarlar"])

st.sidebar.divider()

# Ekran görüntülerindeki filtre yapıları
st.sidebar.subheader("Kişisel Filtreler")
p_filter = st.sidebar.selectbox("Görünüm", 
    ["Tümü", "Favoriler", "Sıradaki (Okunacak)", "Ödünç Verilenler", "Okuma Durumu"])

st.sidebar.subheader("Kitap Filtreleri")
sort_by = st.sidebar.selectbox("Sıralama Ölçütü", ["İsim (A-Z)", "Yazar (A-Z)", "Yeni Eklenenler"])

st.sidebar.subheader("Kategoriler")
cat_filter = st.sidebar.multiselect("Kategori Seç", ["Kategorisiz", "Kişisel", "Profesyonel"], default=["Kategorisiz", "Kişisel", "Profesyonel"])

# --- ANA SAYFA: KÜTÜPHANEM ---
if menu == "🏠 Kütüphanem":
    st.header("Kitaplığım")
    
    # Supabase'den verileri çekme
    query = supabase.table("books").select("*")
    
    # Filtreleri uygulama
    if p_filter == "Favoriler":
        query = query.eq("is_favorite", True)
    elif p_filter == "Sıradaki (Okunacak)":
        query = query.eq("read_next", True)
    elif p_filter == "Ödünç Verilenler":
        query = query.eq("is_loaned", True)
        
    if cat_filter:
        query = query.in_("category", cat_filter)
        
    # Sıralama
    if sort_by == "İsim (A-Z)":
        query = query.order("title")
    elif sort_by == "Yazar (A-Z)":
        query = query.order("author")
    else:
        query = query.order("created_at", desc=True)

    res = query.execute()
    books = res.data

    if not books:
        st.info("Kütüphanenizde henüz kitap bulunmuyor. Sol menüden 'Kitap Ekle' kısmına giderek başlayabilirsiniz.")
    else:
        # Kitapları listeleme (Kart görünümü veya tablo)
        for book in books:
            with st.expander(f"📖 {book['title']} - {book['author']}"):
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.write(f"**Yayınevi:** {book['publisher']}")
                    st.write(f"**Tür:** {book['genre']}")
                    st.write(f"**Kategori:** {book['category']}")
                    st.write(f"**Okuma Durumu:** {book['reading_status']}")
                with col2:
                    if book['is_favorite']: st.write("❤️ Favori")
                    if book['read_next']: st.write("🎯 Sıradaki")
                    if book['is_loaned']: st.write(f"📤 Ödünç: {book['loaned_to']}")
                
                # Güncelleme butonları
                c1, c2, c3 = st.columns(3)
                if c1.button("Favori Yap/Çıkar", key=f"fav_{book['id']}"):
                    supabase.table("books").update({"is_favorite": not book['is_favorite']}).eq("id", book['id']).execute()
                    st.rerun()
                if c2.button("Sıradakine Ekle", key=f"next_{book['id']}"):
                    supabase.table("books").update({"read_next": True}).eq("id", book['id']).execute()
                    st.rerun()
                if c3.button("🗑️ Sil", key=f"del_{book['id']}"):
                    supabase.table("books").delete().eq("id", book['id']).execute()
                    st.rerun()

# --- ANA SAYFA: KİTAP EKLE ---
elif menu == "🔍 Kitap Ekle":
    st.header("Yeni Kitap Ekle")
    
    tab1, tab2 = st.tabs(["📸 Barkod Okut", "✍️ Manuel Giriş"])
    
    with tab1:
        img_file = st.camera_input("Kitap Barkodunu Kameraya Gösterin")
        if img_file:
            img = Image.open(img_file)
            decoded = decode(img)
            if decoded:
                isbn = decoded[0].data.decode('utf-8')
                st.success(f"Barkod okundu: {isbn}")
                
                info = get_book_info(isbn)
                if info:
                    st.json(info)
                    cat = st.selectbox("Kategori Seçin", ["Kategorisiz", "Kişisel", "Profesyonel"], key="barcode_cat")
                    if st.button("Kütüphaneye Kaydet"):
                        info["category"] = cat
                        supabase.table("books").insert(info).execute()
                        st.success("Kitap başarıyla kaydedildi!")
                else:
                    st.warning("Kitap bilgileri bulunamadı, lütfen manuel girin.")
            else:
                st.error("Barkod tespit edilemedi. Lütfen daha net bir görüntü alın.")

    with tab2:
        with st.form("manuel_form"):
            m_title = st.text_input("Kitap Adı*")
            m_author = st.text_input("Yazar")
            m_pub = st.text_input("Yayınevi")
            m_genre = st.text_input("Tür")
            m_cat = st.selectbox("Kategori", ["Kategorisiz", "Kişisel", "Profesyonel"])
            submit = st.form_submit_button("Kaydet")
            
            if submit and m_title:
                data = {
                    "title": m_title,
                    "author": m_author,
                    "publisher": m_pub,
                    "genre": m_genre,
                    "category": m_cat
                }
                supabase.table("books").insert(data).execute()
                st.success("Manuel kayıt başarılı!")

# --- ANA SAYFA: AYARLAR ---
elif menu == "⚙️ Ayarlar":
    st.header("Uygulama Ayarları")
    st.write("Veritabanı bağlantısı aktif.")
    if st.button("Tüm Verileri Temizle (Dikkat!)"):
        st.warning("Bu işlem geri alınamaz!")
        # Güvenlik için buraya onay mekanizması eklenebilir.