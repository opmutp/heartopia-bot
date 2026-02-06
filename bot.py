import os
import json
import traceback
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import tasks
import aiohttp


# ======================
# 고정 설정
# ======================
DEV_GUILD_ID = 1467882843836252411
CHANNEL_ID   = 1467891770451955858
CLUB_ID      = 31555056

# menuId만 뽑아서 씀 (10, 11, 13)
BOARDS = {
    "notice": ("공지사항", 10, "https://cafe.naver.com/f-e/cafes/31555056/menus/10?viewType=L"),
    "update": ("업데이트", 11, "https://cafe.naver.com/f-e/cafes/31555056/menus/11"),
    "event":  ("인게임 이벤트", 13, "https://cafe.naver.com/f-e/cafes/31555056/menus/13"),
}

CHECK_MINUTES = 5
STATE_FILE = "last_seen.json"

TOKEN = os.getenv("TOKEN") or os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN 환경변수가 없음")


# ======================
# discord client
# ======================
intents = discord.Intents.default()

class HeartopiaBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
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
# 위키 커맨드 (유지)
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
# 네이버 카페: 글 목록 JSON API로 최신글 가져오기
# ======================
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Safari/537.36")

async def fetch_latest_article_from_api(menu_id: int, referer: str) -> tuple[int, str] | None:
    """
    성공하면 (article_id, subject) 반환
    """
    api_url = (
        "https://apis.naver.com/cafe-web/cafe2/ArticleList.json"
        f"?search.clubid={CLUB_ID}"
        f"&search.menuid={menu_id}"
        "&search.page=1"
        "&search.perPage=1"
        "&search.sortBy=date"
    )

    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": referer,
    }

    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(api_url, allow_redirects=True) as resp:
            text = await resp.text(errors="ignore")
            print(f"🧾 API menu={menu_id} -> {resp.status} len={len(text)}")
            if resp.status != 200:
                return None
            try:
                data = json.loads(text)
            except Exception:
                return None

    # 구조가 조금씩 달라서 최대한 넓게 탐색
    # 보통은 data["message"]["result"]["articleList"] 같은 형태
    node = data
    for key in ("message", "result"):
        if isinstance(node, dict) and key in node:
            node = node[key]

    article_list = None
    if isinstance(node, dict):
        # 후보 키들
        for k in ("articleList", "articles", "list"):
            if k in node and isinstance(node[k], list):
                article_list = node[k]
                break

    # 못 찾으면 dict 전체를 한번 더 훑어서 리스트 찾기
    if article_list is None and isinstance(node, dict):
        for v in node.values():
            if isinstance(v, list) and v and isinstance(v[0], dict) and ("articleId" in v[0] or "articleid" in v[0]):
                article_list = v
                break

    if not article_list:
        return None

    a = article_list[0]
    article_id = a.get("articleId") or a.get("articleid")
    subject = a.get("subject") or a.get("title") or "새 게시글"

    if not article_id:
        return None

    return int(article_id), str(subject)


def article_link(article_id: int) -> str:
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


async def check_board(state: dict, key: str, board_name: str, menu_id: int, referer: str):
    latest = await fetch_latest_article_from_api(menu_id, referer)
    if not latest:
        print(f"⚠️ {board_name}: API에서 최신 글을 못 가져옴 (권한/차단/구조변경 가능)")
        return

    aid, title = latest
    link = article_link(aid)

    last = state.get(key)

    # 최초 실행은 기준 저장만(스팸 방지)
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
# 루프
# ======================
@tasks.loop(minutes=1)
async def cafe_loop():
    now = datetime.now()
    if now.minute % CHECK_MINUTES != 0:
        return

    print(f"🔁 LOOP TICK {now.isoformat()} (every {CHECK_MINUTES}m)")
    state = load_state()

    for key, (name, menu_id, referer) in BOARDS.items():
        try:
            await check_board(state, key, name, menu_id, referer)
        except Exception as e:
            print(f"❌ {name} 체크 오류:", repr(e))
            traceback.print_exc()


@cafe_loop.before_loop
async def before_cafe_loop():
    print("⏳ cafe_loop: bot ready 대기중...")
    await client.wait_until_ready()
    print("✅ cafe_loop: 시작 준비 완료!")


@client.event
async def on_ready():
    print(f"✅ 봇 로그인 완료: {client.user} / guilds={len(client.guilds)}")

    try:
        await post_embed("SYSTEM", "봇 서버 연결 완료! 자동공지 루프 가동", " ")
    except Exception as e:
        print("❌ 채널 테스트 전송 실패:", repr(e))
        traceback.print_exc()

    if not cafe_loop.is_running():
        cafe_loop.start()
        print("✅ cafe_loop started")


client.run(TOKEN)
