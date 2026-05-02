from ping3 import ping
import feedparser
import time
import requests
import os
import re
from urllib.parse import urlparse
from pydub import AudioSegment

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
    user_podcasts_list = context.user_data.get("podcasts_list", [])
    new_podcast_prof = context.user_data.get("new_podcast_prof")
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
    
    if ep == 'last':
        ep_entry = podcast_feed.entries[0]
    print(f"Downloading episode:{ep}")
    ep_mp3_url = ep_entry.enclosures[0].href

    path = urlparse(ep_mp3_url).path
    filename = os.path.basename(path)
    
    stat_msg = await query.message.reply_text("در حال دانلود اپیزود")
    resp = requests.get(ep_mp3_url, stream=True, timeout=30)
    resp.raise_for_status()
    total_size = int(resp.headers.get("Content-Length", 0))
    await stat_msg.edit_text(f"در حال دانلود اپیزود. (0/{total_size})")

    downloaded_size = 0
    audio_bytes = BytesIO()
    for chunk in resp.iter_content(chunk_size=1024*1024):
        audio_bytes.write(chunk)
        downloaded_size += 1024*1024
        await stat_msg.edit_text(f"در حال دانلود اپیزود. ({downloaded_size}/{total_size})")
  
    audio_bytes.seek(0)
    audio_bytes.name = filename

    await stat_msg.edit_text("دانلود تمام شد...")


    file_size = audio_bytes.getbuffer().nbytes

    audio_seg = AudioSegment.from_file(audio_bytes, format="mp3")
    bitrates = ["128k", "96k", "64k", "48k", "32k"]

    logger.info(f"File size ({file_size / (1024*1024):.2f} MB)")
    if file_size >= 45 * 1024 * 1024:
        logger.info(f"File size too large ({file_size / (1024*1024):.2f} MB)")
        await stat_msg.edit_text("دانلود تمام شد. در حال فشرده سازی...")

        for bitrate in bitrates:
            audio_bytes.seek(0)

            compressed_buffer = BytesIO()
            audio_bytes.export(compressed_buffer, format="mp3", bitrate=bitrate)
            compressed_buffer.seek(0)
            logger.info(f"Tried bitrate {bitrate}: New size = {new_size / (1024*1024):.2f} MB")

            new_size = compressed_buffer.getbuffer().nbytes 

            if new_size < 45 * 1024 * 1024:
                audio_bytes = compressed_buffer
                break
    
    audio_bytes.name = filename

    for i in range(3):
        await stat_msg.edit_text(f"در حال بارگذاری (تلاش {i} از 3)....")
        try:
            await query.message.reply_audio(
                audio=audio_bytes,
                caption="✅ پادکست دانلود شد",
            )
            await stat_msg.delete()
            return
        except Exception as e:
            logger.exception(
                "Failed to parse RSS | user_id=%s | error=%s",
                update.effective_user.id,
                e
            )


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
        .connect_timeout(120)  # افزایش تایم اوت اتصال
        .read_timeout(120)     # افزایش تایم اوت خواندن
        .write_timeout(120)    # افزایش تایم اوت نوشتن
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


