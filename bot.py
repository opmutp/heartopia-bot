import os
import discord
from discord import app_commands
import aiohttp

# Railway 환경변수에서 토큰을 읽음 (GitHub에 노출 안 됨)
TOKEN = os.getenv("TOKEN") or os.getenv("DISCORD_TOKEN")

# 공지 올릴 디스코드 채널 ID
CHANNEL_ID = 1467891770451955858  # 네 채널 ID 그대로 둬

# (선택) 내 서버에서만 슬래시 커맨드 즉시 반영 테스트용
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID")  # 예: "123456789012345678"

# (선택) "1"일 때만 커맨드 sync 실행 (자동배포에서 안전장치)
SYNC_COMMANDS = os.getenv("SYNC_COMMANDS")  # "1" 또는 비워두기


intents = discord.Intents.default()
intents.message_content = True  # 기존 코드 유지 (슬래시에 꼭 필요하진 않음)

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
@client.event
async def setup_hook():
    await tree.sync(guild=discord.Object(id=int(os.getenv("DEV_GUILD_ID"))))


async def wiki_summary(query: str):
    """
    위키백과 요약 가져오기 (ko)
    """
    api = f"https://ko.wikipedia.org/api/rest_v1/page/summary/{query}"

    async with aiohttp.ClientSession() as session:
        async with session.get(api, headers={"User-Agent": "heartopia-bot/1.0"}) as r:
            if r.status != 200:
                return None
            data = await r.json()

    extract = data.get("extract")
    url = (data.get("content_urls", {})
              .get("desktop", {})
              .get("page"))

    if not extract or not url:
        return None

    # 너무 길면 잘라서 디스코드 제한 방지
    if len(extract) > 800:
        extract = extract[:800] + "…"

    return extract, url


@tree.command(name="wiki", description="위키백과에서 검색어 요약을 가져와요")

@app_commands.describe(검색어="예: 서울, 메이플스토리, 양자역학")
async def wiki(interaction: discord.Interaction, 검색어: str):
    await interaction.response.defer(thinking=True, ephemeral=False)

    result = await wiki_summary(검색어)
    if not result:
        await interaction.followup.send(f"‘{검색어}’ 문서를 찾기 어려웠어. 검색어를 조금 바꿔볼래?")
        return

    extract, url = result
    embed = discord.Embed(title=f"위키: {검색어}", description=extract)
    embed.add_field(name="링크", value=url, inline=False)
    await interaction.followup.send(embed=embed)


async def sync_commands_if_needed():
    # 안전장치: 필요할 때만 sync
    if SYNC_COMMANDS != "1":
        return

    # 내 서버에서만 즉시 반영(추천)
    if DEV_GUILD_ID:
        guild = discord.Object(id=int(DEV_GUILD_ID))
        synced = await tree.sync(guild=guild)
        print(f"[SYNC] Guild sync OK: {len(synced)} commands (guild={DEV_GUILD_ID})")
    else:
        # 전역 sync (반영까지 오래 걸릴 수 있음)
        synced = await tree.sync()
        print(f"[SYNC] Global sync OK: {len(synced)} commands")


@client.event
async def on_ready():
    print(f"✅ 봇 로그인 완료! {client.user}")

    # (1) 커맨드 sync (필요할 때만)
    await sync_commands_if_needed()

    # (2) 기존 기능: 연결 메시지 보내기
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("✅ 하트토피아 봇 서버 연결 완료!")
    else:
        print("❌ 채널을 찾을 수 없음")


if not TOKEN:
    raise RuntimeError("❌ TOKEN / DISCORD_TOKEN 환경변수가 비어있어!")

client.run(TOKEN)


