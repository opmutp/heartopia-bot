import os
import re
import json
import traceback
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import tasks
import aiohttp


# ======================
# 고정 설정 (네가 준 값)
# ======================
DEV_GUILD_ID = 1467882843836252411          # 디코 서버(길드) ID
CHANNEL_ID   = 1467891770451955858          # 공지 올릴 채널 ID

CLUB_ID = 31555056

BOARD_URLS = {
    "notice": ("공지사항", "https://cafe.naver.com/f-e/cafes/31555056/menus/10?viewType=L"),
    "update": ("업데이트", "https://cafe.naver.com/f-e/cafes/31555056/menus/11"),
    "event":  ("인게임 이벤트", "https://cafe.naver.com/f-e/cafes/31555056/menus/13"),
}

CHECK_MINUTES = 5
STATE_FILE = "last_seen.json"


# ======================
# 환경변수
# ======================
TOKEN = os.getenv("TOKEN") or os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN 환경변수가 없음 (배포 설정에 TOKEN 넣어줘)")


# ======================
# intents
# ======================
intents = discord.Intents.default()


# ======================
# Client
# ======================
class HeartopiaBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # 길드 sync (반영 빠름)
        await self.tree.sync(guild=discord.Object(id=DEV_GUILD_ID))
        print("✅ 슬래시 커맨드 sync 완료")


client = HeartopiaBot()


# ======================
# 상태 저장/로드
# ======================
def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("❌ 상태 저장 실패:", repr(e))


# ======================
# 위키 슬래시 커맨드 (기존 유지)
# ======================
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


# ======================
# 네이버 카페 가져오기
# ======================
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

async def fetch_html(url: str) -> tuple[int, str]:
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:
        async with session.get(url, allow_redirects=True) as resp:
            text = await resp.text(errors="ignore")
            return resp.status, text


def parse_latest_article(html: str) -> tuple[int, str] | None:
    """
    네이버 카페 페이지 안에 들어있는 articleId/subject(제목) 비슷한 패턴을 최대한 넓게 잡는 파서.
    (네이버가 구조를 바꾸면 더 정교하게 조정해야 할 수 있음)
    """
    if not html:
        return None

    # 1) articleId 먼저 찾기 (가장 흔한 JSON 패턴들)
    # "articleId": 123456  / "articleid": "123456" / articleId=123456 등도 잡음
    id_patterns = [
        r'"articleId"\s*:\s*(\d+)',
        r'"articleid"\s*:\s*"?(\d+)"?',
        r'articleId\s*=\s*(\d+)',
        r'articleid\s*=\s*(\d+)',
    ]
    article_id = None
    for p in id_patterns:
        m = re.search(p, html, flags=re.IGNORECASE)
        if m:
            article_id = int(m.group(1))
            break

    if not article_id:
        return None

    # 2) 제목(subject/title) 비슷한 거 찾기 (없어도 OK)
    title = "새 게시글"
    title_patterns = [
        r'"subject"\s*:\s*"([^"]+)"',
        r'"title"\s*:\s*"([^"]+)"',
        r'"articleTitle"\s*:\s*"([^"]+)"',
    ]
    for p in title_patterns:
        m = re.search(p, html)
        if m:
            title = m.group(1)
            # 너무 긴 제목 컷
            if len(title) > 120:
                title = title[:120] + "…"
            break

    return article_id, title


def article_link(article_id: int) -> str:
    # 새 UI 링크(대체로 잘 열림)
    return f"https://cafe.naver.com/ca-fe/cafes/{CLUB_ID}/articles/{article_id}"


async def post_embed(board_name: str, title: str, link: str):
    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        channel = await client.fetch_channel(CHANNEL_ID)

    embed = discord.Embed(
        title=f"[{board_name}] {title}",
        description=link,
        timestamp=datetime.now(timezone.utc),
    )
    await channel.send(embed=embed)


async def check_one_board(state: dict, key: str, board_name: str, url: str):
    status, html = await fetch_html(url)
    print(f"🌐 {board_name} GET {status} len={len(html)}")

    if status != 200:
        print(f"⚠️ {board_name}: HTTP {status}")
        return

    parsed = parse_latest_article(html)
    if not parsed:
        print(f"⚠️ {board_name}: 최신 글 파싱 실패 (네이버 구조/권한/차단 가능)")
        return

    aid, title = parsed
    link = article_link(aid)

    last = state.get(key)

    # 최초 실행 시: 스팸 방지(기준값만 저장하고 전송은 안 함)
    if not last:
        state[key] = link
        save_state(state)
        print(f"🧷 {board_name}: 초기 기준 저장만 함 -> {link}")
        return

    if last == link:
        print(f"✅ {board_name}: 변경 없음")
        return

    await post_embed(board_name, title, link)
    state[key] = link
    save_state(state)
    print(f"✅ {board_name}: 새 글 전송 완료 -> {link}")


# ======================
# 자동 체크 루프
# ======================
@tasks.loop(minutes=1)
async def cafe_loop():
    # 1분마다 돌되, 실제 체크는 CHECK_MINUTES 배수일 때만
    now = datetime.now()
    if now.minute % CHECK_MINUTES != 0:
        return

    print(f"🔁 LOOP TICK {now.isoformat()} (every {CHECK_MINUTES}m)")

    state = load_state()

    # 게시판별로 독립 try/except (하나 터져도 나머지 진행)
    for key, (name, url) in BOARD_URLS.items():
        try:
            await check_one_board(state, key, name, url)
        except Exception as e:
            print(f"❌ {name} 체크 중 오류:", repr(e))
            traceback.print_exc()


@cafe_loop.before_loop
async def before_cafe_loop():
    print("⏳ cafe_loop: bot ready 대기중...")
    await client.wait_until_ready()
    print("✅ cafe_loop: 시작 준비 완료!")


# ======================
# on_ready
# ======================
@client.event
async def on_ready():
    print(f"✅ 봇 로그인 완료: {client.user} / guilds={len(client.guilds)}")

    # 채널 테스트 전송(1회)
    try:
        await post_embed("SYSTEM", "봇 서버 연결 완료! 자동공지 루프 가동", " ")
    except Exception as e:
        print("❌ 채널 테스트 전송 실패:", repr(e))
        traceback.print_exc()

    # 루프 시작(중복 방지)
    if not cafe_loop.is_running():
        cafe_loop.start()
        print("✅ cafe_loop started")
    else:
        print("⚠️ cafe_loop already running")


# ======================
# run
# ======================
client.run(TOKEN)
