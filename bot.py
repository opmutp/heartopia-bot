import os
import discord

# Railway 환경변수에서 토큰을 읽음 (GitHub에 노출 안 됨)
TOKEN = os.getenv("TOKEN") or os.getenv("DISCORD_TOKEN")


# 공지 올릴 디스코드 채널 ID
CHANNEL_ID = 1467891770451955858  # 네 채널 ID 그대로 둬

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"봇 로그인 완료! {client.user}")
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("✅ 하토피아 봇 서버 연결 완료!")
    else:
        print("❌ 채널을 찾을 수 없음")

client.run(TOKEN)

