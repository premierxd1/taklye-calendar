import discord
import json
import asyncio
from pathlib import Path
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os
import re
from dateutil.parser import isoparse
from aiohttp import web
import threading


TOKEN = os.getenv("DISCORD_TOKEN")
CALENDAR_ID = os.getenv("CALENDAR_ID")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
NOTIFIED_FILE = "notified.json"
ROLE_ID = 1361252742521290866

def load_notified():
    if Path(NOTIFIED_FILE).exists():
        with open(NOTIFIED_FILE, "r") as f:
            return set(json.load(f))
    return set()

already_notified = load_notified()

def save_notified(data):
    with open(NOTIFIED_FILE, "w") as f:
        json.dump(list(data), f)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

SCOPES = ['https://www.googleapis.com/auth/calendar']
creds = service_account.Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
calendar_service = build('calendar', 'v3', credentials=creds)

def get_upcoming_events():
    now = datetime.utcnow().isoformat() + 'Z'
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

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    check_calendar.start()
    monthly_summary_notifier.start()
    # Start the web server
    await create_web_server()

@tasks.loop(seconds=30)
async def check_calendar():
    now = datetime.now(timezone.utc)
    channel = bot.get_channel(CHANNEL_ID)
    events = get_upcoming_events()

    for event in events:
        title = event.get('summary', 'ไม่ระบุชื่อกิจกรรม')
        event_id = event.get('id', 'unknown')
        start_time = event['start'].get('dateTime')
        start = isoparse(start_time) if start_time else isoparse(event['start']['date'] + 'T00:00:00+00:00')
        delta = start - now

        def notify_once(type_name, condition, message):
            noti_key = f"{event_id}|{type_name}"
            if condition and noti_key not in already_notified:
                already_notified.add(noti_key)
                save_notified(already_notified)
                return message
            return None

        th_time = start.astimezone(timezone(timedelta(hours=7)))
        time_24 = th_time.strftime('%H:%M')
        time_12 = th_time.strftime('%I:%M %p')

        messages = [
            notify_once("today", start.astimezone(timezone(timedelta(hours=7))).date() == now.astimezone(timezone(timedelta(hours=7))).date(),
                f"📣 <@&{ROLE_ID}>\n# วันนี้เรามี `{title}` เวลา {time_24} น. ({time_12}) "),
            notify_once("1d", timedelta(hours=23, minutes=59) <= delta <= timedelta(hours=24, minutes=1),
                f"📆 <@&{ROLE_ID}>\n# **พรุ่งนี้** เรามี `{title}` เวลา {time_24} น. ({time_12})"),
            notify_once("1h", timedelta(minutes=59) <= delta <= timedelta(minutes=61),
                f"⏰ <@&{ROLE_ID}>\n# อีก **1 ชั่วโมง** จะถึงเวลา `{title}` เวลา {time_24} น. ({time_12})"),
            notify_once("10m", timedelta(minutes=9, seconds=30) <= delta <= timedelta(minutes=10, seconds=30),
                f"⚠️ <@&{ROLE_ID}>\n# `{title}` เวลา {time_24} น. ({time_12}) จะเริ่มในอีก **10 นาที** เตรียมตัวให้พร้อม!"),
            notify_once("start", timedelta(seconds=-60) < delta < timedelta(seconds=60),
                f"🚀 <@&{ROLE_ID}>\n# ถึงเวลาเริ่ม `{title}` เวลา {time_24} น. ({time_12}) แล้วใครยังไม่มาถ่ายตูดมาให้กูเดี๋ยวนี้!")
        ]

        for msg in messages:
            if msg:
                sent = await channel.send(msg)
                await asyncio.sleep(60)
                try:
                    await sent.delete()
                except:
                    pass

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

async def show_month_events_internal(arg=None):
    try:
        if arg:
            match = re.match(r"(\d{2})/(\d{4})", arg.strip())
            if not match:
                return None
            month, year = map(int, match.groups())
        else:
            now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=7)))
            month, year = now.month, now.year

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

@bot.command(name="ตาราง")
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

@bot.command(name="เพิ่ม")
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


@bot.command(name="ลบ")
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


@bot.command(name="แก้ไข")
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
            if ev_title == title and abs((ev_start - old_utc).total_seconds()) < 60:
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

@bot.command(name="เดือนนี้")
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

from keep_alive import keep_alive

keep_alive()  # ✅ เรียกก่อน เพื่อให้ web server ทำงานก่อนบอท

async def main():
    async with bot:
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())

