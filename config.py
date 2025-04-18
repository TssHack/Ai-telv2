# config.py

# اطلاعات ورود به حساب تلگرام
API_ID = 18377832  # جایگزین شود
API_HASH = "ed8556c450c6d0fd68912423325dd09c"  # جایگزین شود
SESSION_NAME = "my_ai_session" # نام فایل سشن (تغییر داده شد تا با قبلی تداخل نکند)

# کانال‌ها
SOURCE_CHANNEL_ID = -1002102247510   # آیدی عددی کانال منبع
TARGET_CHANNEL_ID = -1002600437794   # آیدی عددی کانال مقصد

# لایسنس‌های API (مثال برای SoundCloud و Divar)
# می‌توانید لایسنس‌های بیشتری اضافه کنید یا ساختار بهتری برای مدیریت آن‌ها در نظر بگیرید
API_KEYS = {
    "wiki_api": [
        "tmPiWoM-6FXRaLt-GwPgLVH-y6g6dHr-dUyLJi3",
        "0ZVxR67-y7Dd6zh-C2jLE21-kY50NYC-GNxiJod",
        "eLwm3cR-2XegSsv-9l9DCta-q4ng622-EeuAsSy",
        "ucGWVCM-S5nHEzi-bss0SdJ-WDwABuG-6YWzWU2",
        "GvrtzqZ-jK3MkEK-NRqhjW1-wvNqXUn-QrKsrDP",
        "5Ti1I4O-SXQ0kp1-qLcZ529-qQ1QgIR-i5QM7oV",
        "3Qk17MU-jc6fJ4g-XFLOCVy-EJlWSmc-c9nxR71",
        "ctorzhF-9vxEUIb-AqybTch-MCJe0Oh-SWx1G7d",
        "lZEcaGZ-5SeK0bS-N6urNSx-tc9WwZO-qJEkcsT",
        "Dg20ygp-6d46CtA-OqEZtv3-CRko2qE-oORjhN0",
        "QabJdKR-4VULYJ4-lOqS19N-FOANKGz-ZuysnYH"
    ]
    # می‌توانید کلیدهای دیگری برای API های دیگر اینجا اضافه کنید
}

# URL های API
API_URLS = {
    "estekhare": "https://stekhare.vercel.app/s",
    "horoscope": "https://open.wiki-api.ir/apis-1/Horoscope/",
    "chart": "https://chart-ehsan.onrender.com/chart",
    "instagram": "https://insta-ehsan.vercel.app/ehsan",
    "pornhub": "https://pp-don-63v4.onrender.com/",
    "divar_search": "https://open.wiki-api.ir/apis-1/SearchDivar/",
    "ai_chat": "https://api.binjie.fun/api/generateStream",
    "soundcloud_dl": "https://open.wiki-api.ir/apis-1/SoundcloudDownloader/",
    "soundcloud_search": "https://open.wiki-api.ir/apis-1/SoundcloudeSearch/"
}

# تنظیمات دیگر
DOWNLOADS_DIR = "downloads" # پوشه برای ذخیره فایل‌های موقت یا دانلود شده
MAX_DIVAR_RESULTS = 10      # حداکثر تعداد نتایج جستجوی دیوار
MAX_SOUNDCLOUD_RESULTS = 8  # حداکثر تعداد نتایج جستجوی ساندکلاد
