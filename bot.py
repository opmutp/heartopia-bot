import os
import discord
from discord import app_commands
import aiohttp

# ===== 환경변수 =====
TOKEN = os.getenv("TOKEN") or os.getenv("DISCORD_TOKEN")
DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID"))      # 서버 ID (길드 ID)
CHANNEL_ID = 1467891770451955858                   # 기존 공지 채널 ID (그대로 유지)

# ===== intents =====
intents = discord.Intents.default()
intents.message_content = True

# ===== Client 클래스 =====
class HeartopiaBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # ⭐ 슬래시 커맨드 등록 (여기서 100% 확정)
        await self.tree.sync(guild=discord.Object(id=DEV_GUILD_ID))
        print("✅ 슬래시 커맨드 sync 완료")

client = HeartopiaBot()

# ===== 위키 기능 =====
async def wiki_summary(query: str):
    url = f"https://ko.wikipedia.org/api/rest_v1/page/summary/{query}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()

    extract = data.get("extract")
    page_url = data.get("content_urls", {}).get("desktop", {}).get("page")
    if not extract or not page_url:
        return None

    if len(extract) > 800:
        extract = extract[:800] + "…"

    return extract, page_url

@client.tree.command(name="wiki", description="위키백과에서 검색어 요약을 가져와요")
@app_commands.describe(query="검색어")
async def wiki(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    result = await wiki_summary(query)

    if not result:
        await interaction.followup.send("❌ 문서를 찾을 수 없어.")
        return

    extract, link = result
    embed = discord.Embed(title=f"위키: {query}", description=extract)
    embed.add_field(name="링크", value=link, inline=False)
    await interaction.followup.send(embed=embed)

# ===== 기존 기능 유지 =====
@client.event
async def on_ready():
    print(f"✅ 봇 로그인 완료: {client.user}")

    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("✅ 하토피아 봇 서버 연결 완료!")
    else:
        print("❌ 채널을 찾을 수 없음")

# ===== 실행 =====
if not TOKEN:
    raise RuntimeError("TOKEN 환경변수가 없음")

client.run(TOKEN)
