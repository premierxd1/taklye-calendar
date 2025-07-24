import discord
import json
import asyncio
from pathlib import Path
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os
import sys
import re
from dateutil.parser import isoparse
from aiohttp import web
import threading
import unicodedata


TOKEN = os.getenv("DISCORD_TOKEN")
CALENDAR_ID = os.getenv("CALENDAR_ID")
CHANNELS_FILE = "channels.json"
NOTIFIED_FILE = "notified.json"
ROLE_ID = 1361252742521290866
VOICE_ID_FILE = "voice_id.json"

SCOPES = ['https://www.googleapis.com/auth/calendar']
creds_json = os.getenv("GOOGLE_CREDS")
creds_dict = json.loads(creds_json)
creds = service_account.Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

calendar_service = build('calendar', 'v3', credentials=creds)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True  
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

def load_channels():
    if Path(CHANNELS_FILE).exists():
        with open(CHANNELS_FILE, "r") as f:
            return json.load(f).get("channel_ids", [])
    return []

def save_channels(channel_ids):
    with open(CHANNELS_FILE, "w") as f:
        json.dump({"channel_ids": channel_ids}, f)

channel_ids = load_channels()

def load_notified():
    if Path(NOTIFIED_FILE).exists():
        with open(NOTIFIED_FILE, "r") as f:
            return set(json.load(f))
    return set()

already_notified = load_notified()

def save_notified(data):
    with open(NOTIFIED_FILE, "w") as f:
        json.dump(list(data), f)

def get_upcoming_events():
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    events_result = calendar_service.events().list(
        calendarId=CALENDAR_ID, timeMin=now,
        maxResults=10, singleEvents=True,
        orderBy='startTime').execute()
    return events_result.get('items', [])

async def create_web_server():
    """Create a simple web server for UptimeRobot monitoring"""
    async def health_check(request):
        return web.Response(text="Bot is running!", status=200)
    
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print("✅ Web server started on port 8080")

async def delete_later(message, delay):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except:
        pass

def save_voice_id(data):
    with open(VOICE_ID_FILE, "w") as f:
        json.dump(data, f)

def load_voice_id():
    if Path(VOICE_ID_FILE).exists():
        with open(VOICE_ID_FILE, "r") as f:
            return json.load(f)
    return {}

async def checkin_members(title, date_str, voice_channel_id, text_channel):
    try:
        role = discord.utils.get(text_channel.guild.roles, id=ROLE_ID)
        voice_channel = text_channel.guild.get_channel(voice_channel_id)

        if not voice_channel:
            return await text_channel.send("❌ ไม่พบห้องพูดคุยที่ตั้งไว้ กรุณาตรวจสอบ `!setvoice`")

        members_in_voice = [member for member in voice_channel.members]
        all_members = [member for member in text_channel.guild.members if role in member.roles]

        lines = []
        for member in all_members:
            symbol = "✅" if member in members_in_voice else "❌"
            lines.append(f"- {member.display_name} {symbol}")

        names = "\n".join(lines) if lines else "ไม่มีใครอยู่ในห้อง"
        message = f"📝 `{title}` {date_str} เช็คชื่อ:\n{names}"
        return await text_channel.send(message)

    except Exception as e:
        print(f"[ERROR-checkin_members] {e}")
        return await text_channel.send("⚠️ เกิดข้อผิดพลาดในการเช็คชื่อ")

async def show_month_events_internal(arg: str = None, *, year: int = None, month: int = None):
    try:
        if isinstance(arg, str) and arg.strip():
            match = re.match(r"(\d{2})/(\d{4})", arg.strip())
            if not match:
                return None
            month, year = map(int, match.groups())
        elif year is not None and month is not None:
            pass  # ใช้ year และ month จาก argument
        else:
            now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=7)))
            year, month = now.year, now.month

        month_names_th = [
            "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
            "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"
        ]
        month_thai = month_names_th[month - 1]

        start_of_month = datetime(year, month, 1, tzinfo=timezone(timedelta(hours=7)))
        next_month = datetime(year + int(month == 12), (month % 12) + 1, 1, tzinfo=timezone(timedelta(hours=7)))

        events_result = calendar_service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=start_of_month.astimezone(timezone.utc).isoformat(),
            timeMax=next_month.astimezone(timezone.utc).isoformat(),
            maxResults=50, singleEvents=True, orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        if not events:
            return f"🫰🏽 ไม่มีทั้งแข่งทั้งซ้อมในเดือน {month_thai} {year}"

        response = f"**📅 ตารางซ้อม/แข่งเดือน {month_thai} {year} :**\n"
        for event in events:
            title = event.get('summary', 'ไม่ระบุชื่อกิจกรรม')
            start = event['start'].get('dateTime', event['start'].get('date'))
            start_dt = datetime.fromisoformat(start)
            th_time = start_dt.astimezone(timezone(timedelta(hours=7)))
            date_str = th_time.strftime('%d/%m/%Y')
            time_24 = th_time.strftime('%H:%M')
            time_12 = th_time.strftime('%I:%M %p')
            response += f"- {title} → {date_str} | {time_24} น. | {time_12}\n"

        return response

    except Exception as e:
        print(f"[ERROR-show_month_events_internal] {e}")
        return "❌ เกิดข้อผิดพลาดในการสร้างตารางอัตโนมัติ"



async def clean_old_calendar_messages():
    for cid in channel_ids:
        channel = bot.get_channel(cid)
        if channel:
            try:
                async for message in channel.history(limit=100):
                    if message.author == bot.user and "📅 ตารางซ้อม/แข่งเดือน" in message.content:
                        await message.delete()
                        print(f"🧹 ลบข้อความตารางเก่าใน {channel.name}")
            except Exception as e:
                print(f"[ERROR-ลบข้อความเก่า] {e}")

async def send_monthly_calendar():
    now = datetime.now(timezone(timedelta(hours=7)))
    year = now.year
    month = now.month
    calendar_text = await show_month_events_internal(arg=f"{month:02d}/{year}")

    if calendar_text:
        for cid in channel_ids:
            channel = bot.get_channel(cid)
            if channel:
                await channel.send(calendar_text)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print("📡 Bot is now online.")

    try:
        await clean_old_calendar_messages()
        await send_monthly_calendar()
        check_calendar.start()
        restart_bot_every_24h.start()
    except Exception as e:
        print(f"[ERROR-on_ready] {e}")

@tasks.loop(hours=24)
async def restart_bot_every_24h():
    print("🔁 รีสตาร์ทบอทเพื่อความเสถียร")
    await asyncio.sleep(2)  # รอให้ print เสร็จก่อน
    os.execv(sys.executable, ['python'] + sys.argv)


@tasks.loop(seconds=30)
async def check_calendar():
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] 🔄 Checking events...")
    events = get_upcoming_events()

    for event in events:
        title = event.get('summary', 'ไม่ระบุชื่อกิจกรรม')
        event_id = event.get('id', 'unknown')
        is_all_day = 'date' in event['start']

        # ✅ แปลงเวลา
        if is_all_day:
            start = isoparse(event['start']['date'] + 'T00:00:00+00:00')
        else:
            start = isoparse(event['start']['dateTime'])

        delta = start - now
        th_time = start.astimezone(timezone(timedelta(hours=7)))
        time_24 = th_time.strftime('%H:%M') if not is_all_day else "ทั้งวัน"
        time_12 = th_time.strftime('%I:%M %p') if not is_all_day else ""

        print(f"🔍 Event: {title} | All-day: {is_all_day} | เวลาไทย: {time_24} | เหลืออีก {delta}")

        def notify_once(type_name, condition, message):
            noti_key = f"{event_id}|{type_name}"
            if condition and noti_key not in already_notified:
                already_notified.add(noti_key)
                save_notified(already_notified)
                print(f"✅ Triggered: {type_name}")
                return message
            return None

        messages = []

        if is_all_day:
            messages.append(notify_once("1d", timedelta(hours=23) <= delta <= timedelta(hours=25),
                f"📆 <@&{ROLE_ID}>\n# **พรุ่งนี้** เรามีกิจกรรมทั้งวัน: `{title}`"))
            messages.append(notify_once("today", th_time.date() == now.astimezone(timezone(timedelta(hours=7))).date(),
                f"📣 <@&{ROLE_ID}>\n# วันนี้มีกิจกรรมทั้งวัน: `{title}`"))
        else:
            messages.extend([
                notify_once("1d", timedelta(hours=23, minutes=59) <= delta <= timedelta(hours=24, minutes=1),
                    f"📆 <@&{ROLE_ID}>\n# **พรุ่งนี้** เรามี `{title}` เวลา {time_24} น. ({time_12})"),
                notify_once("today", th_time.date() == now.astimezone(timezone(timedelta(hours=7))).date(),
                    f"📣 <@&{ROLE_ID}>\n# วันนี้เรามี `{title}` เวลา {time_24} น. ({time_12}) "),
                notify_once("1h", timedelta(minutes=59) <= delta <= timedelta(minutes=61),
                    f"⏰ <@&{ROLE_ID}>\n# อีก **1 ชั่วโมง** จะถึงเวลา `{title}` เวลา {time_24} น. ({time_12})"),
                notify_once("10m", timedelta(minutes=9, seconds=30) <= delta <= timedelta(minutes=10, seconds=30),
                    f"⚠️ <@&{ROLE_ID}>\n# `{title}` เวลา {time_24} น. ({time_12}) จะเริ่มในอีก **10 นาที** เตรียมตัวให้พร้อม!"),
                notify_once("start", timedelta(seconds=-60) < delta < timedelta(seconds=60),
                    f"🚀 <@&{ROLE_ID}>\n# ถึงเวลาเริ่ม `{title}` เวลา {time_24} น. ({time_12}) แล้วใครยังไม่มาถ่ายตูดมาให้กูเดี๋ยวนี้!")
            ])

        delete_times = [86400, 86400, 3600, 600, 300, 300]  # สำหรับ 6 ประเภท
        for msg, delete_after in zip(messages, delete_times):
            if msg:
                for cid in channel_ids:
                    channel = bot.get_channel(cid)
                    if channel:
                        try:
                            sent = await channel.send(msg)
                            print(f"📤 ส่งข้อความไปยัง {channel.name}")
                            asyncio.create_task(delete_later(sent, delete_after))
                        except Exception as e:
                            print(f"[ERROR-ส่งข้อความ] {e}")

        if not is_all_day and timedelta(seconds=-60) < delta < timedelta(seconds=60):
            for cid in channel_ids:
                channel = bot.get_channel(cid)
                if channel:
                    voice_channel_id = load_voice_id().get(str(channel.guild.id))
                    await checkin_members(title, th_time.strftime('%d/%m/%Y'), voice_channel_id, channel)




@tasks.loop(minutes=1)
async def monthly_summary_notifier():
    now = datetime.now(timezone(timedelta(hours=7)))
    if now.day == 1 and now.hour == 0 and now.minute == 0:
        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            return
        month_str = now.strftime("%m/%Y")
        response = await show_month_events_internal(month_str)
        if response:
            bot_msg = await channel.send(response)
            await asyncio.sleep(60)
            await bot_msg.delete()
            async for msg in channel.history(limit=10, before=bot_msg.created_at):
                if msg.author != bot.user:
                    try:
                        await msg.delete()
                        break
                    except:
                        pass

@bot.command(name="today")
async def show_month_events(ctx, *, arg=None):
    response = await show_month_events_internal(arg)
    if response:
        sent = await ctx.send(response)
        await asyncio.sleep(60)
        try:
            await sent.delete()
            await ctx.message.delete()
        except:
            pass

@bot.command(name="addtask")
async def add_event(ctx, *, args):
    try:
        match = re.match(r"(.+)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})", args)
        if not match:
            await ctx.send("❌ รูปแบบคำสั่งไม่ถูกต้อง กรุณาใช้: !เพิ่ม ชื่อกิจกรรม dd/mm/yyyy HH:MM")
            return

        title, date_str, time_str = match.groups()
        date_part = datetime.strptime(date_str, "%d/%m/%Y").date()
        time_part = datetime.strptime(time_str, "%H:%M").time()
        th_dt = datetime.combine(date_part, time_part).replace(tzinfo=timezone(timedelta(hours=7)))
        start_utc = th_dt.astimezone(timezone.utc)

        event = {
            'summary': title,
            'start': {
                'dateTime': start_utc.isoformat(),
                'timeZone': 'UTC',
            },
            'end': {
                'dateTime': (start_utc + timedelta(hours=1)).isoformat(),
                'timeZone': 'UTC',
            },
        }

        calendar_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        await ctx.send(f"✅ เพิ่มกิจกรรม {title} วันที่ {date_str} เวลา {time_str} น. เรียบร้อย")
    except Exception as e:
        await ctx.send("❌ เกิดข้อผิดพลาดในการเพิ่มกิจกรรม")
        print(f"[ERROR-เพิ่ม] {e}")


@bot.command(name="deltask")
async def delete_event(ctx, *, args):
    try:
        match = re.match(r"(.+)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})", args)
        if not match:
            await ctx.send("❌ รูปแบบคำสั่งไม่ถูกต้อง กรุณาใช้: !ลบ ชื่อกิจกรรม dd/mm/yyyy HH:MM")
            return

        title, date_str, time_str = match.groups()
        date_part = datetime.strptime(date_str, "%d/%m/%Y").date()
        time_part = datetime.strptime(time_str, "%H:%M").time()
        target_dt = datetime.combine(date_part, time_part).replace(tzinfo=timezone(timedelta(hours=7)))
        target_utc = target_dt.astimezone(timezone.utc)

        start_utc = datetime.combine(date_part, datetime.min.time()).replace(tzinfo=timezone.utc)
        end_utc = start_utc + timedelta(days=1)

        events_result = calendar_service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=start_utc.isoformat(),
            timeMax=end_utc.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        for event in events:
            ev_title = event.get('summary', '')
            ev_start_str = event['start'].get('dateTime')
            if not ev_start_str:
                continue

            ev_start = isoparse(ev_start_str)
            if ev_title == title and abs((ev_start - target_utc).total_seconds()) < 60:
                calendar_service.events().delete(calendarId=CALENDAR_ID, eventId=event['id']).execute()
                await ctx.send(f"🗑️ ลบกิจกรรม {title} วันที่ {date_str} เวลา {time_str} น. เรียบร้อยแล้ว")
                return

        await ctx.send("⚠️ ไม่พบกิจกรรมที่ต้องการลบ (ชื่อหรือเวลาอาจไม่ตรง)")
    except Exception as e:
        await ctx.send("❌ เกิดข้อผิดพลาดในการลบกิจกรรม")
        print(f"[ERROR-ลบ] {e}")


@bot.command(name="etask")
async def edit_event(ctx, *, args):
    try:
        match = re.match(r"(.+?)\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})(?:\s+(\d{2}/\d{2}/\d{4}))?(?:\s+(\d{2}:\d{2}))?", args)
        if not match:
            await ctx.send("❌ รูปแบบคำสั่งไม่ถูกต้อง กรุณาใช้: !แก้ไข ชื่อ dd/mm/yyyy HH:MM [วันใหม่] [เวลาใหม่]")
            return

        title, old_date_str, old_time_str, new_date_str, new_time_str = match.groups()
        old_date = datetime.strptime(old_date_str, "%d/%m/%Y").date()
        old_time = datetime.strptime(old_time_str, "%H:%M").time()
        old_dt = datetime.combine(old_date, old_time).replace(tzinfo=timezone(timedelta(hours=7)))
        old_utc = old_dt.astimezone(timezone.utc)

        start_utc = datetime.combine(old_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        end_utc = start_utc + timedelta(days=1)

        events = calendar_service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=start_utc.isoformat(),
            timeMax=end_utc.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute().get('items', [])

        for event in events:
            ev_title = event.get('summary', '')
            ev_start_str = event['start'].get('dateTime')
            if not ev_start_str:
                continue

            ev_start = isoparse(ev_start_str)
            norm_ev_title = unicodedata.normalize("NFC", ev_title)
            norm_title = unicodedata.normalize("NFC", title)

            norm_ev_title = unicodedata.normalize("NFC", ev_title)
            norm_title = unicodedata.normalize("NFC", title)

            if norm_ev_title == norm_title and abs((ev_start - target_utc).total_seconds()) < 60:


                # ใช้เวลาเดิม ถ้าไม่มีข้อมูลใหม่
                new_date = datetime.strptime(new_date_str, "%d/%m/%Y").date() if new_date_str else old_date
                new_time = datetime.strptime(new_time_str, "%H:%M").time() if new_time_str else old_time
                new_dt = datetime.combine(new_date, new_time).replace(tzinfo=timezone(timedelta(hours=7)))
                new_utc = new_dt.astimezone(timezone.utc)

                # แก้เวลาใน event เดิม
                event['start']['dateTime'] = new_utc.isoformat()
                event['end']['dateTime'] = (new_utc + timedelta(hours=1)).isoformat()
                calendar_service.events().update(calendarId=CALENDAR_ID, eventId=event['id'], body=event).execute()

                await ctx.send(f"♻️ แก้ไขกิจกรรม {title} เรียบร้อย! → {new_date.strftime('%d/%m/%Y')} {new_time.strftime('%H:%M')} น.")
                return

        await ctx.send("⚠️ ไม่พบกิจกรรมที่ต้องการแก้ไข (ชื่อหรือเวลาอาจไม่ตรง)")
    except Exception as e:
        await ctx.send("❌ เกิดข้อผิดพลาดในการแก้ไขกิจกรรม")
        print(f"[ERROR-แก้ไข] {e}")

@bot.command(name="seetask")
async def this_month_schedule(ctx):
    try:
        # ดึงเดือนและปีปัจจุบัน (เวลาไทย)
        now = datetime.now(timezone(timedelta(hours=7)))
        month_str = now.strftime("%m/%Y")

        # ใช้ฟังก์ชันภายในเพื่อดึงตาราง
        response = await show_month_events_internal(month_str)
        if response:
            await ctx.send(response)

        # ลบข้อความของผู้ใช้ (คำสั่ง)
        await asyncio.sleep(60) #900
        await ctx.message.delete()

    except Exception as e:
        await ctx.send("❌ เกิดข้อผิดพลาดในการแสดงตารางเดือนนี้")
        print(f"[ERROR-เดือนนี้] {e}")

@bot.command(name="add")
async def add_channel(ctx):
    channel_id = ctx.channel.id
    if channel_id not in channel_ids:
        channel_ids.append(channel_id)
        save_channels(channel_ids)
        await ctx.send(f"✅ เพิ่มช่องนี้ในรายการส่งข้อความอัตโนมัติแล้ว")
    else:
        await ctx.send("⚠️ ช่องนี้มีอยู่แล้วในรายการ")

@bot.command(name="remove")
async def remove_channel(ctx):
    channel_id = ctx.channel.id
    if channel_id in channel_ids:
        channel_ids.remove(channel_id)
        save_channels(channel_ids)
        await ctx.send("🗑️ ลบช่องนี้ออกจากรายการสำเร็จแล้ว")
    else:
        await ctx.send("⚠️ ช่องนี้ยังไม่ถูกเพิ่มไว้")

@bot.command(name="setvoice")
async def set_voice_channel(ctx):
    print("⚙️ setvoice เริ่มทำงานแล้ว")
    if ctx.author.voice and ctx.author.voice.channel:
        voice_channel_id = ctx.author.voice.channel.id
        guild_id = str(ctx.guild.id)

        settings = load_voice_id()
        settings[guild_id] = voice_channel_id
        save_voice_id(settings)

        bot_msg = await ctx.send(f"✅ ตั้งค่าห้องพูดคุยสำเร็จ: {ctx.author.voice.channel.name}")
    else:
        bot_msg = await ctx.send("⚠️ กรุณาเข้าห้องพูดคุยก่อนพิมพ์คำสั่งนี้")

    await asyncio.sleep(15)
    try:
        await bot_msg.delete()
        await asyncio.sleep(5)
        await ctx.message.delete()
    except:
        pass


@bot.command(name="check")    
async def test_checkin(ctx):
    settings = load_voice_id()
    voice_channel_id = settings.get(str(ctx.guild.id), 0)

    loading_msg = await ctx.send("📋 กำลังเช็คชื่อ...")
    await asyncio.sleep(2)
    await loading_msg.delete()

    # รอรับข้อความจากฟังก์ชันเช็คชื่อ
    check_msg = await checkin_members(
        "ทดสอบเช็คชื่อ",
        datetime.now().strftime("%d/%m/%Y"),
        voice_channel_id,
        ctx.channel
    )

    await asyncio.sleep(10)
    try:
        await ctx.message.delete()
        await asyncio.sleep(5)
        await check_msg.delete()
    except:
        pass




from keep_alive import keep_alive

keep_alive()  # ✅ เรียกก่อน เพื่อให้ web server ทำงานก่อนบอท

async def main():
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())

