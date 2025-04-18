import asyncio
import re
import subprocess
import aiofiles
import uuid
import httpx
from datetime import datetime
import aiohttp
import os
import random
import logging
from PIL import Image
from io import BytesIO
from telethon import TelegramClient, events
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji
from telethon.errors import MessageNotModifiedError

# وارد کردن تنظیمات از فایل config.py
import config

# تنظیمات لاگ‌گیری
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ایجاد پوشه دانلودها اگر وجود نداشته باشد
os.makedirs(config.DOWNLOADS_DIR, exist_ok=True)

# --- کلاینت تلگرام ---
client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)

# --- وضعیت ربات و مدیریت کلید API ---
robot_status = True
wiki_api_key_index = 0
current_process = None # برای مدیریت فرآیند sms

# --- توابع کمکی ---

def get_next_wiki_api_key():
    """دریافت کلید بعدی از لیست و مدیریت چرخش کلیدها"""
    global wiki_api_key_index
    if not config.API_KEYS["wiki_api"]:
        return None
    key = config.API_KEYS["wiki_api"][wiki_api_key_index]
    wiki_api_key_index = (wiki_api_key_index + 1) % len(config.API_KEYS["wiki_api"])
    return key

async def safe_edit_message(event, message, text, **kwargs):
    """ویرایش پیام با مدیریت خطای MessageNotModifiedError"""
    try:
        await message.edit(text, **kwargs)
    except MessageNotModifiedError:
        pass # اگر پیام تغییری نکرده، خطایی نده
    except Exception as e:
        logger.error(f"Error editing message: {e}")
        # ممکن است بخواهید خطا را به کاربر اطلاع دهید
        # await event.reply(f"خطا در ویرایش پیام: {e}")


def create_progress_bar(percentage: float, width: int = 25) -> str:
    """ایجاد نوار پیشرفت متنی"""
    filled = int(width * percentage / 100)
    empty = width - filled
    bar = '━' * filled + '─' * empty
    return f"[{bar}] {percentage:.1f}%"

async def download_file_async(url: str, session: aiohttp.ClientSession, filename: str):
    """دانلود فایل به صورت ناهمزمان"""
    try:
        async with session.get(url) as response:
            response.raise_for_status() # بررسی خطاهای HTTP
            async with aiofiles.open(filename, 'wb') as f:
                while True:
                    chunk = await response.content.read(8192)
                    if not chunk:
                        break
                    await f.write(chunk)
            return filename
    except aiohttp.ClientError as e:
        logger.error(f"Error downloading file {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error downloading file {url}: {e}")
        return None

# --- توابع مربوط به API ها (بازنویسی شده برای async) ---

async def get_estekhare_async():
    """دریافت لینک تصویر استخاره به صورت ناهمزمان"""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(config.API_URLS["estekhare"]) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("url")
                else:
                    logger.warning(f"Estekhare API returned status {response.status}")
                    return None
        except aiohttp.ClientError as e:
            logger.error(f"Error fetching Estekhare: {e}")
            return None
        except Exception as e: # شامل خطاهای JSON Decode
             logger.error(f"Error processing Estekhare response: {e}")
             return None

async def download_and_process_image_async(img_url, filename_base="estekhare"):
    """دانلود، تبدیل فرمت و تغییر اندازه تصویر به صورت ناهمزمان"""
    temp_filename = f"{config.DOWNLOADS_DIR}/{filename_base}_{uuid.uuid4().hex}.tmp"
    output_filename = f"{config.DOWNLOADS_DIR}/{filename_base}_{uuid.uuid4().hex}.jpg"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(img_url) as response:
                if response.status != 200:
                    logger.warning(f"Image download failed ({response.status}) for URL: {img_url}")
                    return None
                content = await response.read()

            # پردازش تصویر با Pillow (اجرای عملیات CPU-bound در ترد جدا)
            loop = asyncio.get_event_loop()
            def process_image():
                try:
                    image = Image.open(BytesIO(content))
                    image = image.convert("RGB")
                    image.thumbnail((800, 800))
                    image.save(output_filename, format="JPEG", quality=85)
                    return output_filename
                except Exception as pil_error:
                    logger.error(f"Error processing image with Pillow: {pil_error}")
                    return None

            processed_file = await loop.run_in_executor(None, process_image)
            return processed_file

        except aiohttp.ClientError as e:
            logger.error(f"Error downloading image {img_url}: {e}")
            return None
        except Exception as e:
             logger.error(f"Unexpected error downloading/processing image {img_url}: {e}")
             return None
        finally:
             # پاک کردن فایل موقت اگر ایجاد شده بود (این بخش در کد اصلی نبود)
             # if os.path.exists(temp_filename):
             #    os.remove(temp_filename)
             pass # در این پیاده‌سازی فایل موقت نداریم


async def get_horoscope_async():
    """دریافت فال به صورت ناهمزمان"""
    api_key = get_next_wiki_api_key()
    if not api_key:
        return None, "کلید API یافت نشد."

    url = f"{config.API_URLS['horoscope']}?key={api_key}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("detail", {}).get("status") == "success":
                        return data["detail"]["data"], None
                    else:
                        logger.warning(f"Horoscope API success=false: {data.get('detail')}")
                        # اگر کلید مشکل داشت، دوباره امتحان نمی‌کنیم فعلا
                        return None, "خطا در دریافت اطلاعات فال از API."
                elif response.status == 403: # Forbidden - احتمالاً مشکل کلید
                     logger.warning(f"Horoscope API key {api_key} might be invalid (403).")
                     # اینجا می‌توان منطق امتحان کلید بعدی را اضافه کرد
                     return None, "خطا در دسترسی به API فال (کلید نامعتبر؟)"
                else:
                    logger.warning(f"Horoscope API returned status {response.status}")
                    return None, f"خطا در ارتباط با سرور فال ({response.status})."
        except aiohttp.ClientError as e:
            logger.error(f"Error fetching Horoscope: {e}")
            return None, "خطا در اتصال به سرویس فال."
        except Exception as e:
             logger.error(f"Error processing Horoscope response: {e}")
             return None, "خطا در پردازش پاسخ فال."


async def fetch_chart(symbol: str, timeframe: str = '1h') -> str | None:
    """گرفتن چارت از API و ذخیره موقت فایل (بدون تغییر)"""
    chart_url = f"{config.API_URLS['chart']}?symbol={symbol}&timeframe={timeframe}"
    file_name = f"{config.DOWNLOADS_DIR}/{uuid.uuid4().hex}.png"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(chart_url) as resp:
                if resp.status == 200:
                    async with aiofiles.open(file_name, 'wb') as f:
                        await f.write(await resp.read())
                    return file_name
                else:
                    logger.error(f"Chart API error ({resp.status}) for {symbol}/{timeframe}")
                    return None
        except aiohttp.ClientError as e:
             logger.error(f"Error fetching chart for {symbol}/{timeframe}: {e}")
             return None


async def download_and_upload_file(url: str, client: httpx.AsyncClient, event, status_message, file_extension: str, index: int, total_files: int):
    """دانلود و آپلود همزمان فایل (بهبود یافته با هندل خطای بهتر)"""
    temp_filename = f"{config.DOWNLOADS_DIR}/temp_{hash(url)}_{datetime.now().timestamp()}{file_extension}"
    try:
        async with client.stream("GET", url, follow_redirects=True, timeout=120.0) as response: # افزایش تایم اوت
            if response.status_code != 200:
                await safe_edit_message(event, status_message, f"❌ خطا در دانلود فایل {index} - وضعیت: {response.status_code}")
                return

            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            last_update_time = asyncio.get_event_loop().time()

            async with aiofiles.open(temp_filename, 'wb') as f:
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    await f.write(chunk)
                    downloaded += len(chunk)

                    current_time = asyncio.get_event_loop().time()
                    if current_time - last_update_time > 1.0 and total_size > 0: # آپدیت هر ۱ ثانیه
                        last_update_time = current_time
                        percentage = (downloaded / total_size) * 100
                        progress_bar = create_progress_bar(percentage)
                        size_mb = downloaded / (1024 * 1024)
                        total_mb = total_size / (1024 * 1024)
                        await safe_edit_message(event, status_message,
                            f"📥 درحال دانلود فایل {index} از {total_files}...\n"
                            f"{progress_bar}\n"
                            f"💾 {size_mb:.1f}MB / {total_mb:.1f}MB"
                        )

        # آپلود فایل
        last_update_time = 0
        start_upload_time = asyncio.get_event_loop().time()

        async def progress_callback(current, total):
            nonlocal last_update_time
            current_time = asyncio.get_event_loop().time()

            if current_time - last_update_time > 1.0: # آپدیت هر ۱ ثانیه
                last_update_time = current_time
                percentage = (current / total) * 100
                progress_bar = create_progress_bar(percentage)
                size_mb = current / (1024 * 1024)
                total_mb = total / (1024 * 1024)
                elapsed_time = current_time - start_upload_time
                speed_mbps = (size_mb / elapsed_time) if elapsed_time > 0 else 0
                await safe_edit_message(event, status_message,
                    f"📤 درحال آپلود فایل {index} از {total_files}...\n"
                    f"{progress_bar}\n"
                    f"💾 {size_mb:.1f}MB / {total_mb:.1f}MB ({speed_mbps:.2f} MB/s)"
                )

        await event.client.send_file(
            event.chat_id,
            file=temp_filename,
            reply_to=event.message.id,
            supports_streaming=(file_extension == '.mp4'),
            progress_callback=progress_callback
        )
        logger.info(f"Successfully uploaded file {index} from {url}")

    except httpx.RequestError as e:
        logger.error(f"HTTPX Request error during download/upload for file {index} ({url}): {e}")
        await safe_edit_message(event, status_message, f"❌ خطای شبکه در پردازش فایل {index}: {e}")
    except Exception as e:
        logger.exception(f"Error processing file {index} ({url}): {e}") # لاگ کردن traceback
        await safe_edit_message(event, status_message, f"❌ خطای ناشناخته در پردازش فایل {index}: {e}")
    finally:
        if os.path.exists(temp_filename):
            try:
                os.remove(temp_filename)
            except OSError as e:
                logger.error(f"Error removing temp file {temp_filename}: {e}")


async def process_instagram_link(event, message: str, status_message):
    """پردازش یک لینک اینستاگرام (با تلاش مجدد و استفاده از download_and_upload_file)"""
    async with httpx.AsyncClient(timeout=60.0) as http_client:
        for attempt in range(2):
            try:
                api_url = f"{config.API_URLS['instagram']}?url={message}"
                response = await http_client.get(api_url)
                response.raise_for_status() # بررسی خطاهای HTTP

                data = response.json()

                if isinstance(data, dict) and "data" in data and data["data"]:
                    media_items = data["data"]
                    total_files = len(media_items)
                    for index, item in enumerate(media_items, 1):
                        if "media" in item and "type" in item:
                            media_url = item["media"]
                            media_type = item["type"]
                            file_extension = '.jpg' if media_type == "photo" else '.mp4'

                            await download_and_upload_file(
                                media_url,
                                http_client,
                                event,
                                status_message,
                                file_extension,
                                index,
                                total_files
                            )
                        else:
                            logger.warning(f"Instagram item {index} missing 'media' or 'type': {item}")
                            await safe_edit_message(event, status_message, f"⚠️ فایل {index} فاقد لینک یا نوع معتبر است.")
                elif isinstance(data, dict) and data.get("message"): # بررسی پیام خطا از API
                    error_msg = data["message"]
                    logger.error(f"Instagram API error: {error_msg} for url: {message}")
                    await safe_edit_message(event, status_message, f"❌ خطای API اینستاگرام: {error_msg}")
                    return # اگر API خطا داد، تلاش مجدد نکن
                else:
                    logger.warning(f"Invalid data structure from Instagram API: {data}")
                    await safe_edit_message(event, status_message, "❌ داده‌های نامعتبر از API اینستاگرام دریافت شد.")
                    return

                # اگر همه فایل‌ها موفقیت‌آمیز بودند
                await safe_edit_message(event, status_message, "✅ عملیات دانلود و آپلود با موفقیت انجام شد!")
                await asyncio.sleep(5)
                await status_message.delete()
                return

            except httpx.HTTPStatusError as e:
                logger.error(f"Instagram API HTTP error (Attempt {attempt + 1}): {e}")
                error_text = f"❌ خطای HTTP {e.response.status_code} در ارتباط با API اینستاگرام."
            except httpx.RequestError as e:
                 logger.error(f"Instagram API Request error (Attempt {attempt + 1}): {e}")
                 error_text = f"❌ خطای شبکه در ارتباط با API اینستاگرام."
            except ValueError: # JSONDecodeError
                 logger.error(f"Instagram API JSON decode error (Attempt {attempt + 1}) for url: {message}")
                 error_text = "❌ خطا در پردازش پاسخ API اینستاگرام."
            except Exception as e:
                logger.exception(f"Error processing Instagram link (Attempt {attempt + 1}): {e}")
                error_text = f"❌ خطای ناشناخته: {e}"

            # مدیریت تلاش مجدد
            if attempt == 0:
                await safe_edit_message(event, status_message, f"{error_text} در حال تلاش مجدد...")
                await asyncio.sleep(3)
            else:
                await safe_edit_message(event, status_message, f"{error_text} لطفاً بعداً تلاش کنید.")
                # await asyncio.sleep(5)
                # await status_message.delete() # در صورت خطا پیام را نگه دار


async def process_pornhub_link(url):
    """پردازش لینک Pornhub و دریافت لینک‌های دانلود (بهبود یافته)"""
    api_url = f"{config.API_URLS['pornhub']}?url={url}"
    max_retries = 3

    async with aiohttp.ClientSession() as session:
        for attempt in range(max_retries):
            try:
                async with session.get(api_url, timeout=30) as response: # تایم اوت
                    if response.status == 200:
                        data = await response.json()

                        if data.get("code") == 200 and "data" in data:
                            video_data = data["data"]
                            title = video_data.get("title", "بدون عنوان")
                            image = video_data.get("image", "")
                            qualities = video_data.get("video_quality", [])

                            # مرتب‌سازی و فیلتر کردن کیفیت‌ها
                            quality_map = {
                                "426x240": "240p",
                                "854x480": "480p",
                                "1280x720": "720p",
                                "1920x1080": "1080p"
                            }
                            available_links = {}
                            for q in qualities:
                                quality_label = quality_map.get(q.get('type'))
                                quality_url = q.get('url')
                                if quality_label and quality_url:
                                    # انتخاب بهترین لینک برای هر کیفیت (گاهی لینک‌های تکراری با لیبل متفاوت می‌دهد)
                                    if quality_label not in available_links:
                                         available_links[quality_label] = quality_url

                            if not available_links:
                                return "❌ هیچ لینک دانلودی با کیفیت معتبر یافت نشد.", None

                            # ساخت پیام نتیجه
                            result = f"🎬 **{title}**\n\n🔗 **لینک‌های دانلود:**\n"
                            # نمایش به ترتیب کیفیت‌ها
                            for label in ["240p", "480p", "720p", "1080p"]:
                                if label in available_links:
                                    result += f"🔹 **{label}**: [دانلود]({available_links[label]})\n"
                                # else:
                                #     result += f"❌ کیفیت {label} موجود نیست.\n" # نمایش ندادن کیفیت‌های ناموجود

                            return result.strip(), image

                        elif data.get("code") == 600:
                            logger.warning(f"Pornhub API returned code 600 (Attempt {attempt + 1}) for {url}")
                            if attempt < max_retries - 1:
                                await asyncio.sleep(2) # صبر قبل از تلاش مجدد
                                continue
                            else:
                                return "❌ API پردازش لینک با خطا مواجه شد (کد 600). لطفاً دوباره تلاش کنید.", None
                        else:
                            logger.error(f"Pornhub API returned unexpected data (Attempt {attempt + 1}): {data}")
                            return f"❌ پردازش لینک با خطای نامشخص از API مواجه شد (کد: {data.get('code', 'N/A')}).", None

                    else:
                        logger.error(f"Pornhub API request failed (Attempt {attempt + 1}) with status {response.status} for {url}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2)
                            continue
                        else:
                            return f"❌ خطا در ارتباط با API پردازش لینک (وضعیت: {response.status}).", None

            except aiohttp.ClientError as e:
                logger.error(f"Network error processing Pornhub link (Attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
                else:
                    return f"❌ خطای شبکه در پردازش لینک: {e}", None
            except asyncio.TimeoutError:
                 logger.error(f"Timeout processing Pornhub link (Attempt {attempt + 1}) for {url}")
                 if attempt < max_retries - 1:
                     await asyncio.sleep(2)
                     continue
                 else:
                     return "❌ پردازش لینک بیش از حد طول کشید.", None
            except Exception as e:
                logger.exception(f"Unexpected error processing Pornhub link (Attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
                else:
                    return f"❌ خطای پیش‌بینی نشده در پردازش: {e}", None
        # اگر همه تلاش‌ها ناموفق بود
        return "❌ پس از چند بار تلاش، پردازش لینک ناموفق بود.", None

async def search_divar(query, city="tabriz"):
    """جستجو در دیوار به صورت ناهمزمان"""
    api_key = get_next_wiki_api_key()
    if not api_key:
        return None, "کلید API یافت نشد."

    api_url = f"{config.API_URLS['divar_search']}?key={api_key}&city={city}&q={query}"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(api_url, timeout=20) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("status") == True and "detail" in data:
                         results = data["detail"][:config.MAX_DIVAR_RESULTS]
                         if not results:
                             return None, "✅ جستجو انجام شد، اما نتیجه‌ای برای این عبارت یافت نشد."
                         return results, None # None یعنی خطایی رخ نداده
                    else:
                        logger.warning(f"Divar API success=false or no detail: {data}")
                        # اگر کلید مشکل داشت، دوباره امتحان نمی‌کنیم فعلا
                        return None, f"⚠️ API دیوار نتیجه معتبری برنگرداند ({data.get('message', 'خطای نامشخص')})."
                elif response.status == 403:
                     logger.warning(f"Divar API key {api_key} might be invalid (403).")
                     return None, "⚠️ خطا در دسترسی به API دیوار (کلید نامعتبر؟)"
                else:
                    logger.warning(f"Divar API returned status {response.status}")
                    return None, f"⚠️ خطا در ارتباط با سرور دیوار ({response.status})."
        except aiohttp.ClientError as e:
            logger.error(f"Error searching Divar: {e}")
            return None, f"⚠️ خطا در اتصال به سرویس دیوار: {e}"
        except asyncio.TimeoutError:
             logger.error(f"Timeout searching Divar for query: {query}")
             return None, "⚠️ جستجو در دیوار بیش از حد طول کشید."
        except Exception as e:
             logger.exception(f"Error processing Divar search response: {e}")
             return None, f"⚠️ خطا در پردازش پاسخ دیوار: {e}"


async def fetch_api(url, json_data=None, headers=None, method='POST'):
    """تابع عمومی برای درخواست به API ها با aiohttp"""
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            request_method = session.post if method.upper() == 'POST' else session.get
            async with request_method(url, json=json_data) as response:
                if 200 <= response.status < 300:
                    # تلاش برای خواندن به صورت JSON یا متن
                    try:
                        return await response.json() # اگر JSON باشد
                    except aiohttp.ContentTypeError:
                         try:
                             return await response.text() # اگر متن باشد
                         except Exception as text_err:
                             logger.error(f"Error reading text response from {url}: {text_err}")
                             return f"⚠️ خطای خواندن پاسخ متنی: {text_err}"
                    except Exception as json_err:
                        logger.error(f"Error decoding JSON response from {url}: {json_err}")
                        return f"⚠️ خطای پردازش JSON: {json_err}"
                else:
                    error_text = await response.text()
                    logger.warning(f"API request to {url} failed with status {response.status}: {error_text[:200]}") # لاگ کردن بخشی از خطا
                    return f"⚠️ خطای سرور API: {response.status}"
    except aiohttp.ClientError as e:
        logger.error(f"Connection error during API request to {url}: {e}")
        return f"🚫 خطا در اتصال به API: {e}"
    except Exception as e:
        logger.exception(f"Unexpected error during API request to {url}: {e}")
        return f"🚫 خطای پیش‌بینی نشده در درخواست API: {e}"


async def chat_with_ai(query, user_id):
    """چت با هوش مصنوعی بینجی (بهبود یافته)"""
    url = config.API_URLS["ai_chat"]
    headers = {
        "authority": "api.binjie.fun",
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
        "origin": "https://chat18.aichatos.xyz",
        "referer": "https://chat18.aichatos.xyz/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36", # آپدیت User-Agent
        "Content-Type": "application/json"
    }
    data = {
        "prompt": query,
        "userId": f"telegram_{user_id}", # استفاده از یک پیشوند برای تمایز
        "network": True,
        "system": "You are a helpful assistant.", # اضافه کردن System Prompt پایه
        "withoutContext": False,
        "stream": False # چون نیاز به پاسخ کامل داریم
    }
    # این API ممکن است مستقیما متن را برگرداند
    response = await fetch_api(url, json_data=data, headers=headers, method='POST')

    if isinstance(response, str) and response.startswith("⚠️"): # اگر fetch_api خطا برگرداند
        return response
    elif isinstance(response, str): # اگر API مستقیما متن برگرداند
         return response.strip()
    elif isinstance(response, dict): # اگر ساختار JSON داشت (کمتر محتمل بر اساس کد قبلی)
        # اینجا باید ساختار پاسخ API بررسی شود
        return response.get("text", "⚠️ ساختار پاسخ AI نامشخص است.")
    else:
        logger.error(f"Unexpected response type from AI API: {type(response)} - {response}")
        return "⚠️ پاسخ غیرمنتظره از سرویس هوش مصنوعی دریافت شد."


async def download_soundcloud_audio(track_url):
    """دانلود موزیک از SoundCloud با مدیریت کلید API (بهبود یافته)"""
    global wiki_api_key_index # برای ریست کردن در صورت خطا
    
    async with aiohttp.ClientSession() as session:
        attempts = len(config.API_KEYS["wiki_api"])
        if attempts == 0:
             return None, None, None, None, None, None, "هیچ کلید API برای SoundCloud یافت نشد."

        for i in range(attempts):
            api_key = get_next_wiki_api_key() # هر بار کلید بعدی را می‌گیرد
            api_url = f"{config.API_URLS['soundcloud_dl']}?key={api_key}&url={track_url}"
            logger.info(f"Attempting SoundCloud download with key index {wiki_api_key_index-1}")

            try:
                async with session.get(api_url, timeout=45) as response: # افزایش تایم اوت
                    if response.status == 200:
                        data = await response.json()
                        if data.get("status") == True and data.get("detail", {}).get("data"):
                             track_data = data["detail"]["data"]
                             name = track_data.get("name", "نامشخص")
                             artist = track_data.get("artist", "نامشخص")
                             thumb_url = track_data.get("thumb")
                             duration = track_data.get("duration", "نامشخص")
                             date = track_data.get("date", "تاریخ نامشخص")
                             audio_url = track_data.get("dlink")

                             if audio_url:
                                 filename_base = re.sub(r'[\\/*?:"<>|]', "", name) # حذف کاراکترهای نامعتبر از نام فایل
                                 filename = f"{config.DOWNLOADS_DIR}/{filename_base[:50]}.mp3" # محدود کردن طول نام

                                 # دانلود فایل صوتی
                                 logger.info(f"Downloading audio from {audio_url}...")
                                 downloaded_path = await download_file_async(audio_url, session, filename)

                                 if downloaded_path:
                                     logger.info(f"Successfully downloaded {name}")
                                     return downloaded_path, name, artist, thumb_url, duration, date, None # None یعنی بدون خطا
                                 else:
                                     logger.error(f"Failed to download audio file from {audio_url}")
                                     return None, None, None, None, None, None, "خطا در دانلود فایل صوتی."
                             else:
                                 logger.warning(f"No download link (dlink) found in SoundCloud response for {track_url}")
                                 return None, None, None, None, None, None, "لینک دانلود مستقیم در پاسخ API یافت نشد."
                        else:
                            logger.warning(f"SoundCloud DL API success=false or invalid data: {data}")
                            error_msg = data.get("message", "خطای نامشخص از API")
                            # اگر خطا مربوط به کلید بود، به تلاش بعدی برو
                            if "key" in error_msg.lower():
                                logger.warning(f"SoundCloud DL Key {api_key} failed: {error_msg}. Trying next...")
                                continue # برو به کلید بعدی
                            else:
                                # اگر خطای دیگری بود، آن را برگردان
                                return None, None, None, None, None, None, f"خطای API ساندکلاد: {error_msg}"

                    elif response.status == 403: # Forbidden - احتمالاً مشکل کلید
                        logger.warning(f"SoundCloud DL Key {api_key} might be invalid (403). Trying next...")
                        continue # برو به کلید بعدی
                    else:
                        logger.warning(f"SoundCloud DL API returned status {response.status}. Trying next...")
                        continue # شاید مشکل موقتی باشد، با کلید بعدی امتحان کن

            except aiohttp.ClientError as e:
                logger.error(f"Network error downloading SoundCloud track {track_url} (Key: {api_key}): {e}")
                # در صورت خطای شبکه، شاید کلید بعدی کار کند
                continue
            except asyncio.TimeoutError:
                 logger.error(f"Timeout downloading SoundCloud track {track_url} (Key: {api_key})")
                 continue # شاید کلید بعدی سریعتر جواب دهد
            except Exception as e:
                logger.exception(f"Unexpected error downloading SoundCloud track {track_url} (Key: {api_key}): {e}")
                # در خطای ناشناخته، ممکن است مشکل از کلید نباشد، پس برمی‌گردانیم
                return None, None, None, None, None, None, f"خطای پیش‌بینی نشده: {e}"

        # اگر همه کلیدها امتحان شدند و هیچکدام کار نکرد
        logger.error("All SoundCloud API keys failed.")
        return None, None, None, None, None, None, "⚠️ تمام کلیدهای API ساندکلاد ناموفق بودند یا منقضی شده‌اند."

async def search_soundcloud(query):
    """جستجو در SoundCloud با مدیریت کلید API (بهبود یافته)"""
    global wiki_api_key_index

    async with aiohttp.ClientSession() as session:
        attempts = len(config.API_KEYS["wiki_api"])
        if attempts == 0:
            return None, "هیچ کلید API برای SoundCloud یافت نشد."

        for i in range(attempts):
            api_key = get_next_wiki_api_key()
            api_url = f"{config.API_URLS['soundcloud_search']}?key={api_key}&q={query}"
            logger.info(f"Attempting SoundCloud search with key index {wiki_api_key_index-1}")

            try:
                async with session.get(api_url, timeout=20) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("status") == True and "detail" in data:
                            search_results = data.get("detail", {}).get("data", [])

                            if not search_results:
                                return None, "✅ جستجو انجام شد، اما نتیجه‌ای یافت نشد."

                            results = search_results[:config.MAX_SOUNDCLOUD_RESULTS]
                            formatted_results = []
                            for item in results:
                                # بررسی وجود کلیدها قبل از دسترسی
                                title = item.get("title", "بدون عنوان")
                                link = item.get("link")
                                img_url = item.get("img") if item.get("img") != "Not found" else None
                                description = item.get("description") if item.get("description") != "Not found" else "بدون توضیحات"
                                time_info = item.get("time", {})
                                date = time_info.get("date", "تاریخ نامشخص")
                                time = time_info.get("time", "زمان نامشخص")

                                if link: # فقط نتایجی که لینک دارند را اضافه کن
                                     formatted_results.append({
                                        "title": title,
                                        "link": link,
                                        "img": img_url,
                                        "description": description,
                                        "date": date,
                                        "time": time
                                    })

                            if not formatted_results:
                                return None, "✅ نتایجی یافت شد، اما لینک معتبری در آن‌ها نبود."

                            return formatted_results, None # None یعنی بدون خطا

                        else:
                            logger.warning(f"SoundCloud Search API success=false or invalid data: {data}")
                            error_msg = data.get("message", "خطای نامشخص از API")
                            if "key" in error_msg.lower():
                                logger.warning(f"SoundCloud Search Key {api_key} failed: {error_msg}. Trying next...")
                                continue
                            else:
                                return None, f"خطای API جستجوی ساندکلاد: {error_msg}"

                    elif response.status == 403:
                        logger.warning(f"SoundCloud Search Key {api_key} might be invalid (403). Trying next...")
                        continue
                    else:
                        logger.warning(f"SoundCloud Search API returned status {response.status}. Trying next...")
                        continue

            except aiohttp.ClientError as e:
                logger.error(f"Network error searching SoundCloud for '{query}' (Key: {api_key}): {e}")
                continue
            except asyncio.TimeoutError:
                 logger.error(f"Timeout searching SoundCloud for '{query}' (Key: {api_key})")
                 continue
            except Exception as e:
                logger.exception(f"Unexpected error searching SoundCloud for '{query}' (Key: {api_key}): {e}")
                return None, f"خطای پیش‌بینی نشده: {e}"

        logger.error("All SoundCloud API keys failed for search.")
        return None, "⚠️ تمام کلیدهای API ساندکلاد برای جستجو ناموفق بودند یا منقضی شده‌اند."


# --- کنترل‌کننده‌های رویداد (Event Handlers) ---

@client.on(events.NewMessage(pattern='/start', outgoing=True))
async def start_handler(event):
    """پاسخ به دستور /start"""
    await event.edit("👋 سلام! من سلف بات شما هستم.\nبرای دیدن دستورات موجود از `/help` استفاده کنید.")

@client.on(events.NewMessage(pattern='/help', outgoing=True))
async def help_handler(event):
    """نمایش پنل راهنما"""
    help_text = """
    **✨ راهنمای دستورات سلف بات ✨**

    **/on** - روشن کردن ربات
    **/off** - خاموش کردن ربات
    **/status** - نمایش وضعیت ربات (روشن/خاموش)
    **/help** - نمایش این پیام راهنما

    **🔗 دانلودرها:**
    - ارسال لینک اینستاگرام (`instagram.com/...`) - دانلود پست/استوری/ریلز
    - ارسال لینک ساندکلاد (`soundcloud.com/...`) - دانلود موزیک
    - ارسال لینک پورن‌هاب (`pornhub.com/view_video...`) - دریافت لینک‌های دانلود

    **🔎 جستجو:**
    - `ehsan <عبارت>` - جستجوی موزیک در ساندکلاد
    - `divar <عبارت>` - جستجوی آگهی در دیوار (پیش‌فرض: تبریز)
    - `search? <نماد> [تایم‌فریم]` - دریافت چارت ارز دیجیتال (مثال: `search? BTCUSDT 1h`)

    **💬 هوش مصنوعی:**
    - `ai <سوال شما>` - چت با هوش مصنوعی

    **🕋 مذهبی:**
    - `استخاره` - دریافت استخاره با تصویر
    - `فال` - دریافت فال حافظ روزانه

    **🛠️ ابزارها:**
    - ریپلای روی مدیا + `dl` - ذخیره مدیا در Saved Messages
    - `sms? <شماره موبایل>` - اجرای اسکریپت SMS Bomber (نیاز به `sms_encrypted.py`)
    - `stop?` - توقف فرآیند SMS Bomber در حال اجرا

    **🔄 کپی کانال:**
    - پیام‌های کانال منبع به صورت خودکار به کانال مقصد کپی می‌شوند.

    **💾 ذخیره خودکار:**
    - عکس‌ها و ویدیوهای تایم‌ردار به صورت خودکار در Saved Messages ذخیره می‌شوند.
    """
    # استفاده از edit به جای reply برای دستورات خودمان
    await event.edit(help_text, link_preview=False)


@client.on(events.NewMessage(pattern='/on', outgoing=True))
async def on_handler(event):
    """روشن کردن ربات"""
    global robot_status
    robot_status = True
    await event.edit("✅ ربات **روشن** شد!")
    logger.info("Bot turned ON")

@client.on(events.NewMessage(pattern='/off', outgoing=True))
async def off_handler(event):
    """خاموش کردن ربات"""
    global robot_status
    robot_status = False
    await event.edit("❌ ربات **خاموش** شد!")
    logger.info("Bot turned OFF")

@client.on(events.NewMessage(pattern='/status', outgoing=True))
async def status_handler(event):
    """نمایش وضعیت ربات"""
    status_text = "روشن ✅" if robot_status else "خاموش ❌"
    await event.edit(f" وضعیت ربات: **{status_text}**")


# --- پردازشگرهای اصلی ---

@client.on(events.NewMessage(incoming=True, func=lambda e: not e.outgoing)) # فقط پیام‌های ورودی
async def general_message_handler(event):
    """کنترل‌کننده اصلی برای پیام‌های ورودی (غیر از دستورات خاص)"""
    if not robot_status:
        return # اگر ربات خاموش است، هیچ کاری نکن

    # در اینجا می‌توانید کارهای دیگری که برای *همه* پیام‌های ورودی لازم است انجام دهید
    # مثلا لاگ کردن پیام‌ها و ...
    # logger.info(f"Received message in chat {event.chat_id} from {event.sender_id}")
    pass


# --- دانلودرها ---

@client.on(events.NewMessage(pattern=r'.*instagram\.com.*', outgoing=True))
async def handle_instagram(event):
    """پردازش لینک‌های اینستاگرام در پیام‌های خروجی"""
    if not robot_status: return
    message = event.message.text
    # استخراج لینک اصلی از متن
    insta_match = re.search(r'https?://(www\.)?instagram\.com/\S+', message)
    if not insta_match:
        return # اگر لینکی پیدا نشد

    insta_link = insta_match.group(0)
    status_message = await event.edit("🔄 در حال پردازش لینک اینستاگرام... لطفا صبر کنید.")
    await process_instagram_link(event, insta_link, status_message)


@client.on(events.NewMessage(pattern=r'.*soundcloud\.com.*', outgoing=True))
async def handle_soundcloud_link(event):
    """پردازش لینک‌های SoundCloud در پیام‌های خروجی"""
    if not robot_status: return
    message = event.message.text
    soundcloud_match = re.search(r'https?://(www\.)?soundcloud\.com/\S+', message)
    if not soundcloud_match: return

    track_url = soundcloud_match.group(0)
    status_message = await event.edit("🎵 در حال دانلود موزیک از ساندکلاد... لطفاً صبر کنید.")

    file_path, name, artist, thumb_url, duration, date, error = await download_soundcloud_audio(track_url)

    if error:
        await safe_edit_message(event, status_message, f"🚫 {error}")
        return

    if not file_path or not os.path.exists(file_path):
        await safe_edit_message(event, status_message, "🚫 دانلود موزیک انجام شد اما فایل یافت نشد!")
        return

    caption = (
        f"🎶 **آهنگ:** {name}\n"
        f"🎤 **هنرمند:** {artist}\n"
        f"⏳ **مدت زمان:** {duration}\n"
        f"📅 **تاریخ انتشار:** {date}\n"
        f"🔗 [لینک اصلی]({track_url})"
    )

    thumb_file = None
    if thumb_url:
         # دانلود تصویر بندانگشتی
         async with aiohttp.ClientSession() as session:
             thumb_file = await download_file_async(thumb_url, session, f"{config.DOWNLOADS_DIR}/thumb_{uuid.uuid4().hex}.jpg")

    try:
        # نمایش وضعیت آپلود
        await safe_edit_message(event, status_message, f"📤 در حال آپلود موزیک: {name}...")

        # ارسال فایل با استفاده از کلاینت اصلی چون status_message ممکن است حذف شود
        await client.send_file(
            event.chat_id,
            file=file_path,
            caption=caption,
            thumb=thumb_file if thumb_file and os.path.exists(thumb_file) else None,
            reply_to=event.message.id,
            attributes=[types.DocumentAttributeAudio(
                duration=int(duration) if duration.isdigit() else 0, # نیاز به تبدیل به ثانیه دارد اگر فرمت دیگری باشد
                title=name,
                performer=artist
            )] if name and artist else None # ارسال به عنوان فایل صوتی
        )
        await status_message.delete() # حذف پیام وضعیت پس از آپلود موفق

    except Exception as e:
        logger.exception(f"Error sending SoundCloud audio file: {e}")
        await safe_edit_message(event, status_message, f"❗️ خطا در ارسال فایل موزیک: {e}")
    finally:
        # حذف فایل صوتی و تصویر بندانگشتی دانلود شده
        if file_path and os.path.exists(file_path):
            try: os.remove(file_path)
            except OSError as e: logger.error(f"Error removing audio file {file_path}: {e}")
        if thumb_file and os.path.exists(thumb_file):
             try: os.remove(thumb_file)
             except OSError as e: logger.error(f"Error removing thumb file {thumb_file}: {e}")

@client.on(events.NewMessage(pattern=r'.*pornhub\.com/view_video\.php\?viewkey=\S+', outgoing=True))
async def handle_pornhub_link(event):
    """پردازش لینک‌های Pornhub"""
    if not robot_status: return
    message = event.message.text
    url_match = re.search(r'https://(www\.)?pornhub\.com/view_video\.php\?viewkey=\S+', message)
    if not url_match: return

    url = url_match.group(0)
    status_message = await event.edit("⏳ در حال پردازش لینک پورن‌هاب...")

    result_text, image_url = await process_pornhub_link(url)

    image_file = None
    if image_url:
         async with aiohttp.ClientSession() as session:
             image_file = await download_file_async(image_url, session, f"{config.DOWNLOADS_DIR}/ph_thumb_{uuid.uuid4().hex}.jpg")

    try:
        await safe_edit_message(event, status_message, result_text, file=image_file if image_file and os.path.exists(image_file) else None, link_preview=False)
        # پیام وضعیت ویرایش شد، نیازی به حذف جداگانه نیست
    except Exception as e:
        logger.error(f"Error sending Pornhub result: {e}")
        await safe_edit_message(event, status_message, f"خطا در ارسال نتیجه: {e}\n\n{result_text}") # اگر ارسال با عکس نشد، متنش را بفرست
    finally:
         if image_file and os.path.exists(image_file):
             try: os.remove(image_file)
             except OSError as e: logger.error(f"Error removing ph thumb file {image_file}: {e}")


# --- جستجو ---

@client.on(events.NewMessage(pattern=r'^ehsan\s+(.+)', outgoing=True))
async def handle_soundcloud_search(event):
    """جستجو در SoundCloud با دستور ehsan"""
    if not robot_status: return
    query = event.pattern_match.group(1).strip()
    if not query:
        await event.edit("⚠️ لطفا عبارت جستجو را بعد از `ehsan` وارد کنید.")
        return

    status_message = await event.edit(f"🔍 در حال جستجو در ساندکلاد برای: **{query}**...")

    results, error = await search_soundcloud(query)

    if error:
        await safe_edit_message(event, status_message, error)
        return

    if not results:
        await safe_edit_message(event, status_message, "⚠️ هیچ نتیجه‌ای یافت نشد!")
        return

    # پاک کردن پیام وضعیت قبل از ارسال نتایج
    await status_message.delete()

    # ارسال نتایج به صورت جداگانه
    for result in results:
        title = result.get("title", "بدون عنوان")
        link = result.get("link", "بدون لینک")
        img = result.get("img")
        description = result.get("description", "بدون توضیحات")
        date = result.get("date", "تاریخ نامشخص")
        time = result.get("time", "زمان نامشخص")

        caption = (
            f"🎵 **{title}**\n"
            f"📅 **تاریخ:** {date} | ⏰ **زمان:** {time}\n"
            # f"📝 **توضیحات:** {description}\n" # توضیحات معمولا طولانی است
            f"🔗 [مشاهده در ساندکلاد]({link})"
        )
        caption = caption[:1000] # محدود کردن طول کپشن

        try:
             # ارسال با عکس اگر موجود بود، در غیر این صورت فقط متن
             # reply_to را برای اولین نتیجه می‌گذاریم
             reply_to_msg = event.message.id if results.index(result) == 0 else None

             if img:
                  # دانلود موقت عکس برای ارسال
                  img_file = None
                  async with aiohttp.ClientSession() as session:
                     img_file = await download_file_async(img, session, f"{config.DOWNLOADS_DIR}/sc_thumb_{uuid.uuid4().hex}.jpg")

                  if img_file and os.path.exists(img_file):
                     await client.send_file(event.chat_id, img_file, caption=caption, reply_to=reply_to_msg)
                     try: os.remove(img_file)
                     except OSError as e: logger.error(f"Error removing SC thumb file {img_file}: {e}")
                  else:
                     await event.reply(caption, link_preview=False, reply_to=reply_to_msg) # اگر دانلود عکس نشد

             else:
                  await event.reply(caption, link_preview=False, reply_to=reply_to_msg)

        except Exception as e:
             logger.error(f"Error sending SoundCloud search result: {e}")
             # در صورت خطا، پیام را برای کاربر بفرست
             await event.reply(f"خطا در ارسال نتیجه:\n{caption}", link_preview=False)
        await asyncio.sleep(0.5) # کمی تاخیر بین ارسال نتایج


@client.on(events.NewMessage(pattern=r'^divar\s+(.+)', outgoing=True))
async def handle_divar_search(event):
    """جستجو در دیوار با دستور divar"""
    if not robot_status: return
    query = event.pattern_match.group(1).strip()
    city = "tabriz" # یا می‌توانید شهر را هم از دستور بگیرید: divar <شهر> <عبارت>
    if not query:
        await event.edit("⚠️ لطفاً عبارت جستجو را بعد از `divar` وارد کنید.")
        return

    status_message = await event.edit(f"🔍 در حال جستجو برای: **{query}** در دیوار ({city})...")

    results, error = await search_divar(query, city)

    if error:
        await safe_edit_message(event, status_message, error)
        return

    if not results:
        await safe_edit_message(event, status_message, "⚠️ هیچ نتیجه‌ای یافت نشد!")
        return

    # پاک کردن پیام وضعیت
    await status_message.delete()

    # ارسال نتایج
    for result in results:
        title = result.get("title", "بدون عنوان")
        description = result.get("description", "بدون توضیحات")
        price = result.get("price", "توافقی")
        date = result.get("date", "بدون تاریخ")
        link = result.get("link", "بدون لینک")
        image = result.get("image")

        caption = (
            f"📌 **{title}**\n"
            f"💰 **قیمت:** {price}\n"
            f"📜 {description[:150]}...\n" # نمایش بخشی از توضیحات
            f"📅 **تاریخ:** {date}\n"
            f"🔗 [مشاهده آگهی]({link})"
        )
        caption = caption[:1000]

        try:
             reply_to_msg = event.message.id if results.index(result) == 0 else None
             if image and image.startswith("http"):
                 img_file = None
                 async with aiohttp.ClientSession() as session:
                     img_file = await download_file_async(image, session, f"{config.DOWNLOADS_DIR}/divar_img_{uuid.uuid4().hex}.jpg")

                 if img_file and os.path.exists(img_file):
                     await client.send_file(event.chat_id, img_file, caption=caption, reply_to=reply_to_msg)
                     try: os.remove(img_file)
                     except OSError as e: logger.error(f"Error removing divar image file {img_file}: {e}")
                 else:
                     await event.reply(caption, link_preview=False, reply_to=reply_to_msg) # ارسال بدون عکس در صورت خطا دانلود
             else:
                 await event.reply(caption, link_preview=True, reply_to=reply_to_msg) # لینک پیش‌نمایش اگر عکس نبود
        except Exception as e:
             logger.error(f"Error sending Divar result: {e}")
             await event.reply(f"خطا در ارسال نتیجه:\n{caption}", link_preview=False)
        await asyncio.sleep(0.5)


@client.on(events.NewMessage(pattern=r'^search\?\s*(\S+)(?:\s+(\S+))?$', outgoing=True))
async def handle_chart_search(event):
    """دریافت چارت ارز دیجیتال"""
    if not robot_status: return
    symbol = event.pattern_match.group(1).upper()
    timeframe = event.pattern_match.group(2) or '1h' # اگر تایم‌فریم نبود، پیش‌فرض 1h

    status_message = await event.edit(f"📈 در حال دریافت چارت **{symbol}** در تایم‌فریم **{timeframe}**...")

    file_path = await fetch_chart(symbol, timeframe)

    if file_path and os.path.exists(file_path):
        try:
            await client.send_file(
                event.chat_id,
                file=file_path,
                caption=f"📊 چارت **{symbol}** - تایم‌فریم **{timeframe}**",
                reply_to=event.id
            )
            await status_message.delete() # حذف پیام وضعیت
        except Exception as e:
            logger.error(f"Error sending chart file: {e}")
            await safe_edit_message(event, status_message, f"❌ خطا در ارسال فایل چارت: {e}")
        finally:
             try: os.remove(file_path)
             except OSError as e: logger.error(f"Error removing chart file {file_path}: {e}")
    else:
        await safe_edit_message(event, status_message, f"❌ خطا در دریافت چارت برای **{symbol}** ({timeframe}). لطفاً از معتبر بودن نماد و تایم‌فریم مطمئن شوید.")


# --- هوش مصنوعی ---

@client.on(events.NewMessage(pattern=r'^[aA][iI]\s+(.+)', outgoing=True))
async def handle_ai_chat(event):
    """ارسال پیام به هوش مصنوعی"""
    if not robot_status: return
    query = event.pattern_match.group(1).strip()
    user_id = event.sender_id # یا می‌توانید یک ID ثابت بدهید

    if not query:
        await event.edit("⚠️ لطفا سوال یا درخواست خود را بعد از `ai` بنویسید.")
        return

    status_message = await event.edit("🧠 هوش مصنوعی در حال پردازش درخواست شما...")

    try:
        response_text = await chat_with_ai(query, user_id)
        response_text = response_text.strip() if response_text else "⚠️ پاسخی از هوش مصنوعی دریافت نشد!"

        # ارسال پاسخ (با کنترل طول پیام)
        max_length = 4000 # نزدیک به محدودیت تلگرام
        if len(response_text) > max_length:
             response_chunks = [response_text[i:i+max_length] for i in range(0, len(response_text), max_length)]
             await safe_edit_message(event, status_message, response_chunks[0]) # ویرایش پیام اول با بخش اول
             for chunk in response_chunks[1:]:
                 await event.reply(chunk) # ارسال بخش‌های بعدی به صورت ریپلای
        else:
             await safe_edit_message(event, status_message, response_text)

    except Exception as e:
        logger.exception(f"Error in AI chat processing: {e}")
        await safe_edit_message(event, status_message, f"🚫 خطا در پردازش درخواست هوش مصنوعی: {e}")


# --- مذهبی ---

@client.on(events.NewMessage(pattern='(?i)^استخاره$', outgoing=True))
async def send_estekhare(event):
    """ارسال استخاره"""
    if not robot_status: return
    status_message = await event.edit("📖 در حال دریافت استخاره...")

    img_url = await get_estekhare_async()

    if img_url:
        image_file = await download_and_process_image_async(img_url, "estekhare_img")
        if image_file and os.path.exists(image_file):
            try:
                await client.send_file(event.chat_id, file=image_file, reply_to=event.id)
                await status_message.delete() # حذف پیام وضعیت
            except Exception as e:
                logger.error(f"Error sending Estekhare image: {e}")
                await safe_edit_message(event, status_message, "❌ خطایی در ارسال تصویر استخاره رخ داد.")
            finally:
                 try: os.remove(image_file)
                 except OSError as e: logger.error(f"Error removing Estekhare image file {image_file}: {e}")
        else:
            await safe_edit_message(event, status_message, "❌ خطا در دانلود یا پردازش تصویر استخاره.")
    else:
        await safe_edit_message(event, status_message, "❌ خطا در دریافت اطلاعات استخاره از سرور.")


@client.on(events.NewMessage(pattern='^فال$', outgoing=True))
async def send_horoscope(event):
    """ارسال فال حافظ"""
    if not robot_status: return
    status_message = await event.edit("📜 در حال دریافت فال حافظ...")

    horoscope_data, error = await get_horoscope_async()

    if error:
        await safe_edit_message(event, status_message, f"❌ {error}")
        return

    if horoscope_data:
        faal_text = horoscope_data.get("faal", "متن فال یافت نشد.")
        taabir_text = horoscope_data.get("taabir", "تعبیر یافت نشد.")
        img_url = horoscope_data.get("img")
        audio_url = horoscope_data.get("audio")

        caption = (
            f"**📜 فال حافظ امروز شما:**\n\n{faal_text}\n\n"
            f"**✨ تعبیر:**\n{taabir_text}"
        )
        caption = caption[:1000] # محدودیت کپشن عکس

        image_file = None
        if img_url:
            image_file = await download_and_process_image_async(img_url, "horoscope_img")

        audio_file = None
        if audio_url:
             async with aiohttp.ClientSession() as session:
                 audio_file = await download_file_async(audio_url, session, f"{config.DOWNLOADS_DIR}/horoscope_audio.mp3")

        try:
             # ارسال عکس و متن
             if image_file and os.path.exists(image_file):
                 await client.send_file(event.chat_id, image_file, caption=caption, parse_mode='markdown', reply_to=event.id)
             else:
                 await event.reply(caption, parse_mode='markdown') # اگر عکس نبود، فقط متن

             # ارسال فایل صوتی اگر وجود داشت
             if audio_file and os.path.exists(audio_file):
                 await client.send_file(event.chat_id, audio_file, title="فایل صوتی فال", reply_to=event.id)

             await status_message.delete() # حذف پیام وضعیت

        except Exception as e:
             logger.error(f"Error sending horoscope: {e}")
             await safe_edit_message(event, status_message, f"❌ خطایی در ارسال فال رخ داد: {e}")
        finally:
             # حذف فایل‌های موقت
             if image_file and os.path.exists(image_file):
                 try: os.remove(image_file)
                 except OSError as e: logger.error(f"Error removing horoscope image file {image_file}: {e}")
             if audio_file and os.path.exists(audio_file):
                 try: os.remove(audio_file)
                 except OSError as e: logger.error(f"Error removing horoscope audio file {audio_file}: {e}")
    else:
        await safe_edit_message(event, status_message, "❌ متاسفانه مشکلی در دریافت اطلاعات فال پیش آمده است.")


# --- ابزارها ---

@client.on(events.NewMessage(pattern=r'^dl$', outgoing=True))
async def save_media_manual(event):
    """ذخیره مدیا در Saved Messages با دستور dl"""
    if not robot_status: return

    if not event.is_reply:
        await event.edit("⚠️ لطفا روی یک پیام دارای **مدیا** ریپلای کنید و سپس `dl` را بفرستید.")
        return

    replied_message = await event.get_reply_message()

    if replied_message and replied_message.media:
        status_message = await event.edit("📥 در حال دانلود مدیا برای ذخیره...")
        try:
            # استفاده از مسیر دانلود پیش‌فرض تلگرام + نام فایل اصلی
            file_path = await replied_message.download_media(file=config.DOWNLOADS_DIR + "/")
            if file_path and os.path.exists(file_path):
                 await safe_edit_message(event, status_message, "📤 در حال آپلود مدیا به Saved Messages...")
                 await client.send_file(
                     "me", # ارسال به Saved Messages
                     file_path,
                     caption=f"📥 مدیا ذخیره شد از چت: {await event.get_chat()}\n"
                             f"🔗 [لینک پیام اصلی](https://t.me/c/{event.chat_id}/{replied_message.id})"
                 )
                 await safe_edit_message(event, status_message, "✅ مدیا با موفقیت در **Saved Messages** ذخیره شد.")
                 await asyncio.sleep(5)
                 await status_message.delete()
            else:
                 await safe_edit_message(event, status_message, "❌ خطا در دانلود مدیا.")

        except Exception as e:
            logger.error(f"Error in manual save (dl): {e}")
            await safe_edit_message(event, status_message, f"❌ خطا در ذخیره مدیا: {e}")
        finally:
            # تلاش برای پاک کردن فایل دانلود شده اگر وجود داشت
             if 'file_path' in locals() and file_path and os.path.exists(file_path):
                 try: os.remove(file_path)
                 except OSError as e: logger.error(f"Error removing downloaded media file {file_path}: {e}")
    else:
        await event.edit("⚠️ پیامی که ریپلای کردید **مدیا** ندارد.")


@client.on(events.NewMessage(pattern=r'^sms\?\s*(\d{10,})$', outgoing=True))
async def sms_handler(event):
    """اجرای اسکریپت SMS Bomber"""
    if not robot_status: return
    global current_process
    phone_number = event.pattern_match.group(1)

    # چک کردن وجود فایل اسکریپت
    script_path = 'sms_encrypted.py' # یا هر نامی که هست
    if not os.path.exists(script_path):
         await event.edit(f"❌ فایل اسکریپت `{script_path}` یافت نشد!")
         return

    if current_process and current_process.poll() is None:
        await event.edit("⏳ یک فرآیند SMS Bomber در حال اجراست. برای توقف از `stop?` استفاده کنید.")
        return

    await event.edit(f"💣 در حال اجرای SMS Bomber برای شماره: `{phone_number}`...")
    logger.info(f"Starting SMS Bomber for {phone_number}")

    try:
        # اجرای اسکریپت در پس‌زمینه
        # اطمینان حاصل کنید که اسکریپت sms_encrypted.py قابلیت اجرایی دارد (chmod +x)
        current_process = subprocess.Popen(
            ['python3', script_path, phone_number],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True # برای دریافت خروجی به صورت متن
        )
        await event.edit(f"💥 فرآیند SMS Bomber برای `{phone_number}` آغاز شد. برای توقف از `stop?` استفاده کنید.\n"
                         f"(خروجی اسکریپت در لاگ‌ها نمایش داده می‌شود)")

        # می‌توانید منتظر بمانید و خروجی را بگیرید، اما ممکن است طولانی باشد
        # stdout, stderr = await current_process.communicate() # این خط برنامه را مسدود می‌کند تا فرآیند تمام شود

        # یا می‌توانید خروجی را به صورت غیرمسدود بخوانید (پیچیده‌تر)

    except FileNotFoundError:
         await event.edit(f"❌ دستور `python3` یافت نشد. لطفاً پایتون 3 را نصب کنید.")
         current_process = None
    except Exception as e:
        logger.exception(f"Error running SMS Bomber script: {e}")
        await event.edit(f"❌ خطا در اجرای فایل SMS Bomber: {e}")
        current_process = None

@client.on(events.NewMessage(pattern=r'^stop\?$', outgoing=True))
async def stop_sms_handler(event):
    """توقف فرآیند SMS Bomber"""
    if not robot_status: return
    global current_process

    if current_process and current_process.poll() is None: # اگر فرآیند وجود دارد و در حال اجراست
        try:
            current_process.terminate() # ارسال سیگنال SIGTERM
            # منتظر ماندن برای چند ثانیه تا فرآیند بسته شود
            try:
                 current_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                 logger.warning("SMS Bomber process did not terminate gracefully, killing...")
                 current_process.kill() # ارسال سیگنال SIGKILL اگر بسته نشد

            await event.edit("🛑 فرآیند SMS Bomber متوقف شد.")
            logger.info("SMS Bomber process terminated by user.")
        except Exception as e:
            logger.error(f"Error terminating SMS Bomber process: {e}")
            await event.edit(f"❌ خطا در توقف فرآیند: {e}")
        finally:
             current_process = None
    else:
        await event.edit("🤷‍♂️ هیچ فرآیند SMS Bomber فعالی برای توقف وجود ندارد.")


# --- ذخیره خودکار و کپی کانال ---

@client.on(events.NewMessage(incoming=True)) # بررسی همه پیام‌های ورودی
async def auto_features_handler(event):
    """مدیریت ذخیره خودکار مدیا تایمردار و کپی کانال"""
    if not robot_status: return

    # 1. ذخیره خودکار مدیا تایمردار در Saved Messages
    #    (فقط در چت‌های خصوصی و گروه‌ها، نه کانال‌ها)
    if not event.is_channel: # اطمینان از اینکه پیام در کانال نیست
        save_caption = None
        file_to_save = None
        is_ttl_media = False
        original_filename = None

        if event.photo and hasattr(event.photo, "ttl_seconds") and event.photo.ttl_seconds:
             is_ttl_media = True
             original_filename = f"ttl_photo_{event.date.strftime('%Y%m%d_%H%M%S')}.jpg"
             save_caption = f"🖼️ عکس تایمردار ({event.photo.ttl_seconds}s) از {event.sender.first_name or 'کاربر'}\nچت: {await event.get_chat()}\n{event.date.strftime('%Y-%m-%d %H:%M:%S')}"

        elif event.video and hasattr(event.video, "ttl_seconds") and event.video.ttl_seconds:
            is_ttl_media = True
            # تلاش برای گرفتن نام فایل اصلی اگر وجود داشت
            filename_attr = next((attr for attr in event.video.attributes if hasattr(attr, 'file_name')), None)
            original_filename = filename_attr.file_name if filename_attr else f"ttl_video_{event.date.strftime('%Y%m%d_%H%M%S')}.mp4"
            save_caption = f"📹 ویدیوی تایمردار ({event.video.ttl_seconds}s) از {event.sender.first_name or 'کاربر'}\nچت: {await event.get_chat()}\n{event.date.strftime('%Y-%m-%d %H:%M:%S')}"

        if is_ttl_media:
            logger.info(f"Detected self-destructing media from {event.sender_id} in chat {event.chat_id}")
            download_path = os.path.join(config.DOWNLOADS_DIR, original_filename)
            try:
                file_to_save = await event.download_media(file=download_path)
                if file_to_save and os.path.exists(file_to_save):
                    logger.info(f"Downloading {original_filename} for auto-save...")
                    await client.send_file("me", file_to_save, caption=save_caption)
                    logger.info(f"Auto-saved {original_filename} to Saved Messages.")
                else:
                     logger.error("Failed to download self-destructing media for auto-save.")
            except Exception as e:
                 logger.error(f"Error auto-saving self-destructing media: {e}")
            finally:
                if file_to_save and os.path.exists(file_to_save):
                    try: os.remove(file_to_save)
                    except OSError as e: logger.error(f"Error removing auto-saved file {file_to_save}: {e}")

    # 2. کپی پیام از کانال منبع به مقصد
    if event.chat_id == config.SOURCE_CHANNEL_ID:
        logger.info(f"Copying message {event.message.id} from source channel {config.SOURCE_CHANNEL_ID}")
        try:
            # ارسال مجدد پیام به جای فوروارد کردن برای حذف نام فرستنده اصلی
            # await event.message.forward_to(config.TARGET_CHANNEL_ID) # روش ساده‌تر با نام فرستنده

            # روش کپی محتوا (بدون نام فرستنده اصلی)
            await client.send_message(
                config.TARGET_CHANNEL_ID,
                message=event.message # ارسال کل پیام با مدیا و متن
            )
            # یا می‌توانید جداگانه بفرستید:
            # if event.media:
            #     await client.send_file(
            #         config.TARGET_CHANNEL_ID,
            #         file=event.media,
            #         caption=event.text or ""
            #     )
            # elif event.text:
            #     await client.send_message(config.TARGET_CHANNEL_ID, event.text)

        except Exception as e:
            logger.error(f"Error copying message {event.message.id} from source to target channel: {e}")


# --- اجرا ---

async def main():
    """تابع اصلی برای اجرای ربات"""
    try:
        await client.start(phone=lambda: input("لطفا شماره تلفن خود را وارد کنید: "),
                           password=lambda: input("لطفا رمز عبور دوم (در صورت وجود) را وارد کنید: "),
                           code_callback=lambda: input("لطفا کد ارسال شده به تلگرام را وارد کنید: "))
        logger.info("Client started successfully!")
        my_info = await client.get_me()
        logger.info(f"Logged in as: {my_info.first_name} (ID: {my_info.id})")

        print("="*20)
        print(f"🤖 ربات سلف {my_info.first_name} فعال شد!")
        print("✅ وضعیت اولیه: روشن")
        print(f"📖 برای راهنمایی دستور /help را در یکی از چت‌های خود ارسال کنید.")
        print("="*20)

        await client.run_until_disconnected()

    except Exception as e:
        logger.exception(f"An error occurred during startup or runtime: {e}")
    finally:
        if client.is_connected():
            await client.disconnect()
            logger.info("Client disconnected.")

if __name__ == "__main__":
    asyncio.run(main())
