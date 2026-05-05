from ping3 import ping
import feedparser
import time
import requests
import os
import re
from urllib.parse import urlparse
import subprocess
import tempfile

from io import BytesIO

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    error,
    constants
)

from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    BaseHandler,
    CallbackQueryHandler,
    filters,
    PicklePersistence
    )

import asyncio
from dotenv import load_dotenv
load_dotenv()


import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger(__name__)

BALE_BASE_URL = "https://tapi.bale.ai/"


async def new_podcast_request_feed(update : Update, context : ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لطفا RSS feed پادکست خود را وارد کنید:")
    return 'GET_RSS_ADD'


def get_podcast_key(rss_url):
    return rss_url.strip().lower()


async def compress_audio_ffmpeg(input_path, target_bitrate="96k"):
    """فشرده‌سازی با ffmpeg - مصرف RAM ثابت"""
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            output_path = tmp.name
        
        cmd = [
            "ffmpeg", "-i", input_path,
            "-b:a", target_bitrate,
            "-y", "-loglevel", "error",
            output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        
        if result.returncode == 0:
            return output_path, os.path.getsize(output_path)
        else:
            if os.path.exists(output_path):
                os.unlink(output_path)
            logger.error(f"ffmpeg failed: {result.stderr.decode()}")
            return None, 0
            
    except Exception as e:
        logger.exception(f"Compression failed: {e}")
        if 'output_path' in locals() and os.path.exists(output_path):
            os.unlink(output_path)
        return None, 0


async def new_podcast_add(update : Update, context : ContextTypes.DEFAULT_TYPE):
    wait_msg = await update.message.reply_text("لطفا صبر کنید...")
    url = update.message.text
    try:
        feed = feedparser.parse(url)
    except Exception as e:
        await wait_msg.edit_text("خطا در پردازش RSS feed. لطفا مطمئن شوید که URL معتبر است.")
        return ConversationHandler.END
    
    if feed.bozo and feed.bozo_exception:
        await wait_msg.edit_text("خطا در پردازش RSS feed. لطفا مطمئن شوید که URL معتبر است.")
        return 'GET_RSS_ADD'
    
    podcast_title = feed.feed.title
    podcast_subtitle = feed.feed.subtitle if 'subtitle' in feed.feed else "بدون توضیحات"
    podcast_image_url = feed.feed.image.href if 'image' in feed.feed else None

    podcast_prof = {
        'rss_url': url,
        'title' : podcast_title,
        'description' : podcast_subtitle,
        'image' : podcast_image_url
    }

    # Send podcast details to user
    message_text = f"**{podcast_title}**\n\nتوضیحات: {podcast_subtitle}"

    inline_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("تایید", callback_data="add_new_podcast")],
        [InlineKeyboardButton("بازگشت", callback_data="reject_new_podcast")]
        ])

    if podcast_image_url:
        try:
            await update.message.reply_photo(
            photo=podcast_image_url,
            caption=message_text,
            reply_markup=inline_keyboard)

        except Exception as e:
            print(f"Exception getting image {podcast_image_url} : {e}")
            print(f"Trying download and upload...")

            try:
                img_response = requests.get(podcast_image_url, timeout=15)
                img_response.raise_for_status()
                photo_bytes = BytesIO(img_response.content)
                photo_bytes.name = "cover.jpg"
                await update.message.reply_photo(
                    photo=photo_bytes,
                    caption=message_text,
                    reply_markup=inline_keyboard
                )
            except Exception as e:
                print(f"All download attempts failed: {e}")
                await update.message.reply_text(message_text, reply_markup=inline_keyboard)

    else:        
        await update.message.reply_text(message_text)
    context.user_data.update({'new_podcast_prof':podcast_prof})
    await wait_msg.delete()
    return 'AWAIT_ACCEPT'


async def cancel_add_podcast(update : Update, context : ContextTypes.DEFAULT_TYPE):
    return ConversationHandler.END


async def add_new_podcast(update : Update, context : ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    new_podcast_prof = context.user_data.get("new_podcast_prof")
    podcast_key = get_podcast_key(new_podcast_prof['rss_url'])
    
    # اضافه کردن به bot_data اگر وجود نداشته باشد
    if "all_podcasts" not in context.bot_data:
        context.bot_data["all_podcasts"] = {}
    
    if podcast_key not in context.bot_data["all_podcasts"]:
        context.bot_data["all_podcasts"][podcast_key] = new_podcast_prof
        (context.bot_data["all_podcasts"][podcast_key]).update({'downloaded_episodes':{}})
        logger.info(f"Added new podcast to bot_data: {new_podcast_prof['title']}")
    
    # اضافه کردن به لیست کاربر
    user_podcasts_list = context.user_data.get("podcasts_list", [])
    user_podcasts_list.append(new_podcast_prof)
    context.user_data["podcasts_list"] = user_podcasts_list
    context.user_data.update({"new_podcast_prof": []})

    keyboard = ReplyKeyboardMarkup([[
        KeyboardButton("پادکست‌های من")
    ]])
    await query.message.reply_text("پادکست به لیست پادکت‌های شما اضافه شد", reply_markup=keyboard)
    return ConversationHandler.END


async def reject_new_podcast(update : Update, context : ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.update({"new_podcast_prof": []})
    return ConversationHandler.END


async def listen_podcast_episode(update : Update, context : ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    match = re.match(r"^listenPodcastEpisode_(\d+)_(last|\d+)$", data)
    ind = int(match.group(1))
    ep = match.group(2)

    podcast_feed = feedparser.parse(context.user_data.get("podcasts_list")[ind]['rss_url'])
    if podcast_feed.bozo and podcast_feed.bozo_exception:
        await query.message.reply_text(f"RSS در دسترس نیست.")
        return
    
    pod_key = get_podcast_key(context.user_data.get("podcasts_list")[ind]['rss_url'])
    podcast_data = context.bot_data['all_podcasts'].get(pod_key, {})

    downloaded_episodes = podcast_data.get('downloaded_episodes', {})

    entry_idx = 0
    if ep == 'last':
        entry_idx = 0
    else:
        entry_idx = int(ep)
    
    ep_entry = podcast_feed.entries[entry_idx]
    ep_itunes_episode = ep_entry.get('itunes_episode', f"ep_{entry_idx}")
    ep_title = ep_entry.title

    # مرحله 1: بررسی file_id
    if ep_itunes_episode in downloaded_episodes:
        logger.info(f"Episode {ep_itunes_episode} found in cache")
        downloaded_episode_data = downloaded_episodes[ep_itunes_episode]

        fid = downloaded_episode_data.get("file_id")
        if fid:
            try:
                logger.info(f"Attempting to send using file_id: {fid}")
                sent_msg = await query.message.reply_audio(
                    audio=fid,
                    caption=f"✅ {ep_title}",
                )
                logger.info(f"Successfully sent episode using file_id")
                return
            except error.BadRequest as e:
                logger.warning(f"file_id invalid or expired: {e}")
                downloaded_episodes[ep_itunes_episode].pop("file_id", None)
            except error.NetworkError as e:
                logger.warning(f"Network error when sending with file_id: {e}")
            except Exception as e:
                logger.exception(f"Failed to send with file_id: {e}")
    
        # مرحله 2: بررسی فایل کش شده
        downloaded_episode_file_path = downloaded_episode_data.get("file_path")
        if downloaded_episode_file_path and os.path.exists(downloaded_episode_file_path):
            try:
                logger.info(f"Attempting to send cached file: {downloaded_episode_file_path}")
                with open(downloaded_episode_file_path, "rb") as f:
                    sent_msg = await query.message.reply_audio(
                        audio=f,
                        caption=f"✅ {ep_title}",
                    )
                
                # ذخیره file_id جدید
                new_file_id = sent_msg.audio.file_id
                downloaded_episodes[ep_itunes_episode]["file_id"] = new_file_id
                context.bot_data['all_podcasts'][pod_key]['downloaded_episodes'] = downloaded_episodes
                logger.info(f"Sent cached file and saved new file_id: {new_file_id}")
                return
            except error.NetworkError as e:
                logger.warning(f"Network error when sending cached file: {e}")
            except Exception as e:
                logger.exception(f"Failed to send cached file: {e}")
    
    # مرحله 3: دانلود جدید
    logger.info(f"Downloading episode: {ep_itunes_episode}")
    ep_mp3_url = ep_entry.enclosures[0].href
    
    path = urlparse(ep_mp3_url).path
    filename = os.path.basename(path)
    if not filename.endswith('.mp3'):
        filename = f"{ep_itunes_episode}.mp3"
    
    stat_msg = await query.message.reply_text("در حال دانلود اپیزود...")
    
    try:
        resp = requests.get(ep_mp3_url, stream=True, timeout=30)
        resp.raise_for_status()
        total_size = int(resp.headers.get("Content-Length", 0))
        
        # ذخیره موقت روی دیسک
        os.makedirs("downloads/temp", exist_ok=True)
        temp_path = os.path.join("downloads/temp", f"temp_{filename}")
        
        downloaded_size = 0
        last_update = 0
        
        with open(temp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024*1024):
                f.write(chunk)
                downloaded_size += len(chunk)
                
                # به‌روزرسانی پیام هر 5 مگابایت
                if downloaded_size - last_update >= 5 * 1024 * 1024:
                    await stat_msg.edit_text(
                        f"در حال دانلود اپیزود... ({downloaded_size / (1024*1024):.1f}/{total_size / (1024*1024):.1f} MB)"
                    )
                    last_update = downloaded_size
      
        await stat_msg.edit_text("دانلود تمام شد...")

        file_size = os.path.getsize(temp_path)
        logger.info(f"Downloaded file size: {file_size / (1024*1024):.2f} MB")

        # فشرده‌سازی در صورت نیاز
        final_path = temp_path
        if file_size >= 20 * 1024 * 1024:
            logger.info(f"File too large, compressing with ffmpeg...")
            await stat_msg.edit_text("در حال فشرده‌سازی...")

            bitrates = ["128k", "96k", "64k", "48k"]
            compressed = False

            for bitrate in bitrates:
                await stat_msg.edit_text(f"در حال فشرده‌سازی (bitrate:{bitrate})...")
                compressed_path, new_size = await compress_audio_ffmpeg(temp_path, bitrate)
                
                if compressed_path and new_size < 20 * 1024 * 1024:
                    final_path = compressed_path
                    file_size = new_size
                    compressed = True
                    logger.info(f"Compressed to {bitrate}: {new_size / (1024*1024):.2f} MB")
                    break
                elif compressed_path:
                    os.unlink(compressed_path)

                logger.info(f"Still too large ({file_size / (1024*1024):.2f}), compressing with ffmpeg...")
            
            if not compressed:
                os.unlink(temp_path)
                await stat_msg.edit_text("❌ فایل بعد از فشرده‌سازی هنوز بزرگ است")
                return
        
        # ذخیره فایل در کش
        os.makedirs("downloads", exist_ok=True)
        cache_file_path = os.path.join("downloads", filename)
        
        if final_path != cache_file_path:
            os.rename(final_path, cache_file_path)
            if final_path == temp_path and os.path.exists(temp_path):
                pass  # فایل قبلا rename شده
            elif os.path.exists(temp_path):
                os.unlink(temp_path)
        
        logger.info(f"Saved to cache: {cache_file_path}")

        if 'downloaded_episodes' not in context.bot_data['all_podcasts'][pod_key]:
            context.bot_data['all_podcasts'][pod_key]['downloaded_episodes'] = {}
        
        context.bot_data['all_podcasts'][pod_key]['downloaded_episodes'][ep_itunes_episode] = {
            "file_id": None,
            "file_path": cache_file_path,
            "title": ep_title
        }

        # ارسال فایل
        for attempt in range(3):
            await stat_msg.edit_text(f"در حال بارگذاری (تلاش {attempt + 1} از 3)...")
            try:
                with open(cache_file_path, "rb") as f:
                    sent_msg = await query.message.reply_audio(
                        audio=f,
                        caption=f"✅ {ep_title}",
                    )
                
                # ذخیره file_id
                new_file_id = sent_msg.audio.file_id
                context.bot_data['all_podcasts'][pod_key]['downloaded_episodes'][ep_itunes_episode]["file_id"] = new_file_id
                
                logger.info(f"Successfully uploaded and saved file_id: {new_file_id}")
                await stat_msg.delete()
                return
                
            except error.NetworkError as e:
                logger.warning(f"Network error on attempt {attempt + 1}: {e}")
                if attempt < 2:
                    await asyncio.sleep(2)
            except Exception as e:
                logger.exception(f"Upload failed on attempt {attempt + 1}: {e}")
                if attempt < 2:
                    await asyncio.sleep(2)
        
        await stat_msg.edit_text("❌ خطا در بارگذاری فایل بعد از 3 تلاش")
        
    except requests.RequestException as e:
        logger.exception(f"Download failed: {e}")
        await stat_msg.edit_text("❌ خطا در دانلود فایل")
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        await stat_msg.edit_text("❌ خطای غیرمنتظره")
    finally:
        # پاکسازی فایل‌های موقت
        if 'temp_path' in locals() and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except:
                pass


async def podcast_preview_message(ind, podcast, update):
    """
    """
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("همه اپیزودها", callback_data=f"listPodcastEpisodes_{ind}")],
        [InlineKeyboardButton("آخرین اپیزود", callback_data=f"listenPodcastEpisode_{ind}_last")]
    ])

    feed = feedparser.parse(podcast['rss_url'])
    if feed.bozo and feed.bozo_exception:
        await update.message.reply_text(f"RSS در دسترس نیست.")
        return
    
    last_episode = feed.entries[0]
    last_episode_title = last_episode.title

    message = f"""*{podcast['title']}*

{podcast['description']}

آخرین اپیزود: {last_episode_title}
    """

    if podcast['image']:
        img_response = requests.get(podcast['image'], timeout=15)
        img_response.raise_for_status()
        photo_bytes = BytesIO(img_response.content)
        photo_bytes.name = "cover.jpg"
        await update.message.reply_photo(
            photo=photo_bytes,
            caption=message,
            reply_markup=keyboard
        )
        return
    
    await update.message.reply_text(
        text=message,
        reply_markup=keyboard
    )


async def user_podcasts_list(update : Update, context : ContextTypes.DEFAULT_TYPE):
    podcasts = context.user_data.get("podcasts_list")
    
    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton("پادکست جدید ➕ ")]
    ])

    if not podcasts:
        await update.message.reply_text("شما هیچ پادکستی ندارید. برای اضافه کردن پادکست روی `پادکست جدید ➕ ` کلیک کنید.", reply_markup=keyboard)

    else:
        await update.message.reply_text("لیست پادکست‌های شما:.", reply_markup=keyboard)
        for ind, podcast in enumerate(podcasts):
            await podcast_preview_message(ind, podcast, update)


async def start_cmd(update : Update, context : ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup([[
        KeyboardButton("پادکست‌های من")
    ]])

    await update.message.reply_text("سلام!\nبه ربات پادکست دانلودر خوش اومدید!!!\nپادکست دلخواهت رو اضافه بکن و بهشون گوش کن.", reply_markup=keyboard)


if __name__=="__main__":
    app = (
        ApplicationBuilder()
        .token(os.getenv("BOT_TOKEN"))
        .base_url(BALE_BASE_URL)
        .connect_timeout(120)
        .read_timeout(120)
        .write_timeout(120)
        .pool_timeout(120)
        .build()
    )
    app.add_handler(CommandHandler('start', start_cmd))

    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & filters.Regex("^پادکست‌های من$"), callback=user_podcasts_list))

    add_podcast_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & filters.Regex("^پادکست جدید ➕"), callback=new_podcast_request_feed)],
        states={
            'GET_RSS_ADD':[MessageHandler(filters.TEXT & filters.Regex(r'(https?://[^\s]+)'), callback=new_podcast_add)],
            'AWAIT_ACCEPT':[CallbackQueryHandler(callback = add_new_podcast, pattern="^add_new_podcast$"),
                            CallbackQueryHandler(callback = reject_new_podcast, pattern="^reject_new_podcast$")]
        },
        fallbacks=[CommandHandler('cancel', cancel_add_podcast)]
    )

    app.add_handler(CallbackQueryHandler(callback=listen_podcast_episode, pattern=r"^listenPodcastEpisode_(\d+)_(last|\d+)$"))
    app.add_handler(add_podcast_conv)
    app.run_polling(allowed_updates=Update.ALL_TYPES)
