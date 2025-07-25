import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone
import asyncio
import json
import os
import re
from dateutil.parser import isoparse
from flask import Flask
from threading import Thread
import sys
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- Setup ---
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
ROLE_ID = int(os.getenv("DISCORD_ROLE_ID"))
CALENDAR_ID = os.getenv("CALENDAR_ID")
channel_ids = json.loads(os.getenv("CHANNEL_IDS", "[]"))

already_notified = set()
restart_time = datetime.now(timezone.utc) + timedelta(hours=24)

# --- Google Calendar Setup ---
creds_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
creds_dict = json.loads(creds_json)
creds = service_account.Credentials.from_service_account_info(
    creds_dict,
    scopes=['https://www.googleapis.com/auth/calendar.readonly']
)
calendar_service = build('calendar', 'v3', credentials=creds)

def load_voice_id():
    try:
        with open("voice_channel.json", "r") as f:
            return json.load(f)
    except:
        return {}

def save_voice_id(data):
    with open("voice_channel.json", "w") as f:
        json.dump(data, f)

def save_notified(data):
    with open("notified.json", "w") as f:
        json.dump(list(data), f)

def get_upcoming_events():
    now = datetime.utcnow().isoformat() + 'Z'
    time_max = (datetime.utcnow() + timedelta(days=30)).isoformat() + 'Z'

    events_result = calendar_service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=now,
        timeMax=time_max,
        maxResults=20,
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    return events_result.get('items', [])

async def delete_later(message, delay):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except:
        pass

@tasks.loop(seconds=30)
async def check_calendar():
    global restart_time
    now = datetime.now(timezone.utc)
    print(f"[{now.isoformat()}] 🔄 Checking events...")

    # ตรวจสอบการรีสตาร์ท
    if now >= restart_time:
        print("🔁 รีสตาร์ทบอทเพื่อความเสถียร")
        os.execv(sys.executable, ['python'] + sys.argv)

    events = get_upcoming_events()

    for event in events:
        title = event.get('summary', 'ไม่ระบุชื่อกิจกรรม')
        event_id = event.get('id', 'unknown')
        start_time = event['start'].get('dateTime')
        allday = False

        if start_time:
            start = isoparse(start_time)
        else:
            allday = True
            start = isoparse(event['start']['date'] + 'T00:00:00+00:00')

        delta = start - now

        th_time = start.astimezone(timezone(timedelta(hours=7)))
        time_24 = th_time.strftime('%H:%M')
        time_12 = th_time.strftime('%I:%M %p')

        print(f"🔍 Event: {title} | All-day: {allday} | เวลาไทย: {time_24} | เหลืออีก {delta}")

        def notify_once(type_name, condition, message):
            noti_key = f"{event_id}|{type_name}"
            if condition and noti_key not in already_notified:
                already_notified.add(noti_key)
                save_notified(already_notified)
                print(f"✅ Triggered: {type_name}")
                return message
            return None

        messages = []

        if allday:
            messages.append(notify_once("allday_1d",
                th_time.date() - timedelta(days=1) == now.astimezone(timezone(timedelta(hours=7))).date(),
                f"📆 <@&{ROLE_ID}>\n# **พรุ่งนี้** เรามี `{title}` (ทั้งวัน)"))

            messages.append(notify_once("allday_today",
                th_time.date() == now.astimezone(timezone(timedelta(hours=7))).date(),
                f"📣 <@&{ROLE_ID}>\n# วันนี้เรามี `{title}` (ทั้งวัน)"))
        else:
            messages.append(notify_once("1d",
                timedelta(hours=23) <= delta <= timedelta(hours=25),
                f"📆 <@&{ROLE_ID}>\n# **พรุ่งนี้** เรามี `{title}` เวลา {time_24} น. ({time_12})"))

            messages.append(notify_once("today",
                th_time.date() == now.astimezone(timezone(timedelta(hours=7))).date(),
                f"📣 <@&{ROLE_ID}>\n# วันนี้เรามี `{title}` เวลา {time_24} น. ({time_12})"))

            messages.append(notify_once("1h",
                timedelta(minutes=59) <= delta <= timedelta(minutes=61),
                f"⏰ <@&{ROLE_ID}>\n# อีก **1 ชั่วโมง** จะถึงเวลา `{title}` เวลา {time_24} น. ({time_12})"))

            messages.append(notify_once("10m",
                timedelta(minutes=9, seconds=30) <= delta <= timedelta(minutes=10, seconds=30),
                f"⚠️ <@&{ROLE_ID}>\n# `{title}` เวลา {time_24} น. ({time_12}) จะเริ่มในอีก **10 นาที** เตรียมตัวให้พร้อม!"))

            messages.append(notify_once("start",
                timedelta(seconds=-60) < delta < timedelta(seconds=60),
                f"🚀 <@&{ROLE_ID}>\n# ถึงเวลาเริ่ม `{title}` เวลา {time_24} น. ({time_12}) แล้วใครยังไม่มาถ่ายตูดมาให้กูเดี๋ยวนี้!"))

        delete_times = [86400, 86400, 86400, 3600, 600, 300, 300]
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

        if timedelta(seconds=-60) < delta < timedelta(seconds=60):
            for cid in channel_ids:
                channel = bot.get_channel(cid)
                if channel:
                    voice_channel_id = load_voice_id().get(str(channel.guild.id))
                    await checkin_members(title, th_time.strftime('%d/%m/%Y'), voice_channel_id, channel)

# -- start task --
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print("📡 Bot is now online.")
    check_calendar.start()