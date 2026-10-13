#!/usr/bin/env python3
"""비스트로미 인스타 승인 봇.

GitHub Actions가 10분마다 실행한다. 한 번 실행될 때마다:
  1. 텔레그램 버튼(올리기/보류)과 명령(/list 등)을 읽어 상태에 반영
  2. 게시 24시간 전에 미리보기를, 2시간 전까지 결정이 없으면 알림을 보냄
  3. 승인된 게시물 중 예정 시각이 지난 것을 인스타그램에 올림
상태는 state.json에 저장되고 워크플로가 커밋한다.

사용: python3 bot.py [run|check|preview-next]
DRY_RUN=1 이면 네트워크 호출 없이 동작만 출력한다.
"""
import hashlib, json, os, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.abspath(__file__))
POSTS_FILE = os.path.join(ROOT, "posts.json")
STATE_FILE = os.path.join(ROOT, "state.json")
TOKEN_FILE = os.path.join(ROOT, "token.enc")
REPO = os.environ.get("GITHUB_REPOSITORY", "yoonsp79/bistrome-insta")
BRANCH = os.environ.get("GITHUB_REF_NAME") or "main"
RAW = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/"
IG_API = "https://graph.instagram.com/" + os.environ.get("IG_API_VERSION", "v23.0")
PREVIEW_LEAD = timedelta(hours=24)
REMIND_LEAD = timedelta(hours=2)
REFRESH_EVERY = timedelta(days=7)
MAX_TRIES = 3
DOW = "월화수목금토일"
DRY = os.environ.get("DRY_RUN") == "1"
TOKEN = ""


def now():
    return datetime.fromisoformat(os.environ["FAKE_NOW"]) if os.environ.get("FAKE_NOW") else datetime.now(KST)


def when(p):
    t = datetime.fromisoformat(p["publish_at"])
    return f"{t.month}/{t.day}({DOW[t.weekday()]}) {t:%H:%M}"


# ---------------------------------------------------------------- HTTP

def secrets():
    vals = [TOKEN, os.environ.get("IG_ACCESS_TOKEN", ""), os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TOKEN_KEY", "")]
    return [v for v in vals if v and len(v) >= 8]


def scrub(text):
    """로그, 텔레그램, state.json(공개 저장소)에 남는 글에서 비밀값을 지운다."""
    text = str(text)
    for v in secrets():
        text = text.replace(v, "***")
    return text


def mask(value):
    """GitHub Actions 로그에서 이 값을 가리도록 등록한다 (갱신된 토큰은 시크릿이 아니라 자동으로 가려지지 않음)."""
    if value and os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::add-mask::{value}", flush=True)


def http(method, url, params=None, json_body=None, form=None):
    if DRY:
        shown = {k: ("***" if "token" in k.lower() else v) for k, v in (json_body or form or params or {}).items()}
        print(scrub(f"[dry] {method} {url} {shown}")[:300])
        return {"ok": True, "result": {"message_id": 1}, "id": "dry", "status_code": "FINISHED", "data": []}
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body, headers = None, {}
    if json_body is not None:
        body, headers = json.dumps(json_body).encode(), {"Content-Type": "application/json"}
    elif form is not None:
        body = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:  # 오류 본문만 남기고 토큰이 든 URL은 남기지 않는다
        raise RuntimeError(scrub(f"HTTP {e.code}: {e.read().decode(errors='replace')[:400]}")) from None
    except Exception as e:  # 연결 오류 등: 메시지에 주소가 섞일 수 있어 비밀값을 지우고 다시 던진다
        raise RuntimeError(scrub(f"{type(e).__name__}: {e}")[:400]) from None


def tg(method, **data):
    r = http("POST", f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{method}", json_body=data)
    if not r.get("ok"):
        raise RuntimeError(scrub(f"telegram {method}: {r}"))
    return r["result"]


def tg_safe(method, **data):
    try:
        return tg(method, **data)
    except Exception as e:
        print(scrub(f"telegram {method} 실패: {e}"))


def say(text, **extra):
    return tg("sendMessage", chat_id=CHAT, text=text, disable_web_page_preview=True, **extra)


def ig(method, path, **params):
    params["access_token"] = TOKEN
    url = f"{IG_API}/{path}"
    return http("GET", url, params=params) if method == "GET" else http("POST", url, form=params)


# ---------------------------------------------------------------- token

def openssl(text, decrypt=False):
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-a", "-A", "-pass", "env:TOKEN_KEY"]
    if decrypt:
        args.append("-d")
    return subprocess.run(args, input=text.encode(), capture_output=True, check=True).stdout.decode().strip()


def load_token(state):
    """token.enc(갱신된 토큰)를 우선 쓰되, GitHub 시크릿의 토큰이 바뀌었으면 그쪽을 쓴다."""
    env = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    env_hash = hashlib.sha256(env.encode()).hexdigest()[:16] if env else ""
    if env and state.get("env_token_hash") != env_hash:
        state["env_token_hash"] = env_hash
        state.pop("token_refreshed", None)
        save_token(env)
        return env
    if os.environ.get("TOKEN_KEY") and os.path.exists(TOKEN_FILE):
        try:
            return openssl(open(TOKEN_FILE).read(), decrypt=True)
        except Exception as e:
            print("token.enc 복호화 실패 (TOKEN_KEY가 바뀌었는지 확인)")
    return env


def save_token(tok):
    if os.environ.get("TOKEN_KEY") and not DRY:
        with open(TOKEN_FILE, "w") as f:
            f.write(openssl(tok) + "\n")


def refresh_token(state):
    global TOKEN
    last, failed = state.get("token_refreshed"), state.get("token_refresh_failed")
    if last and now() - datetime.fromisoformat(last) < REFRESH_EVERY:
        return
    if failed and now() - datetime.fromisoformat(failed) < timedelta(hours=24):
        return
    try:
        r = http("GET", "https://graph.instagram.com/refresh_access_token",
                 params={"grant_type": "ig_refresh_token", "access_token": TOKEN})
        if r.get("access_token") and not DRY:
            TOKEN = r["access_token"]
            mask(TOKEN)
            save_token(TOKEN)
        state["token_refreshed"] = now().isoformat()
        if r.get("expires_in"):
            state["token_expires"] = (now() + timedelta(seconds=int(r["expires_in"]))).isoformat()
        state.pop("token_warned", None)
        state.pop("token_refresh_failed", None)
    except Exception as e:
        print(scrub(f"토큰 갱신 실패: {e}"))
        state["token_refresh_failed"] = now().isoformat()
        exp = state.get("token_expires")
        if exp and datetime.fromisoformat(exp) - now() < timedelta(days=10) and not state.get("token_warned"):
            say("⚠️ 인스타 연결 토큰이 곧 만료됩니다. 메타 개발자 페이지에서 토큰을 새로 만들어 GitHub 시크릿 IG_ACCESS_TOKEN을 바꿔 주세요.")
            state["token_warned"] = True


_UID = None
def ig_user_id():
    global _UID
    if _UID is None:
        me = ig("GET", "me", fields="user_id,username")
        _UID = str(me.get("user_id") or me.get("id"))
    return _UID


# ---------------------------------------------------------------- telegram UI

def buttons(pid, status):
    if status == "approved":
        row = [{"text": "⏸ 보류로 바꾸기", "callback_data": f"hold:{pid}"}]
    elif status == "held":
        row = [{"text": "✅ 올리기", "callback_data": f"ok:{pid}"}]
    else:
        row = [{"text": "✅ 올리기", "callback_data": f"ok:{pid}"}, {"text": "⏸ 보류", "callback_data": f"hold:{pid}"}]
    return {"inline_keyboard": [row]}


STATUS_LINE = {
    "pending": "🕒 결정 대기 중",
    "approved": "✅ 승인됨 · 예정 시각에 올라갑니다",
    "held": "⏸ 보류됨 · 올리지 않습니다",
    "published": "🎉 게시 완료",
    "failed": "⚠️ 게시 실패",
}


def preview_text(p, st):
    n = len(p["images"])
    head = f"{STATUS_LINE[st['status']]}\n📅 {when(p)} 게시 예정\n{p['title']} · 사진 {n}장"
    if st.get("permalink"):
        head += f"\n{st['permalink']}"
    return f"{head}\n\n────────\n{p['caption']}"


def refresh_preview(p, st):
    if not st.get("preview_msg"):
        return
    extra = {} if st["status"] in ("published", "failed") else {"reply_markup": buttons(p["id"], st["status"])}
    try:
        tg("editMessageText", chat_id=CHAT, message_id=st["preview_msg"], text=preview_text(p, st),
           disable_web_page_preview=True, **extra)
    except Exception as e:
        if "not modified" not in str(e):
            print(scrub(f"미리보기 수정 실패 {p['id']}: {e}"))


def send_preview(p, st):
    urls = [RAW + i for i in p["images"]]
    if len(urls) == 1:
        tg("sendPhoto", chat_id=CHAT, photo=urls[0])
    else:
        tg("sendMediaGroup", chat_id=CHAT, media=[{"type": "photo", "media": u} for u in urls])
    msg = say(preview_text(p, st), reply_markup=buttons(p["id"], st["status"]))
    st["preview_msg"] = msg["message_id"]
    st["preview_sent"] = now().isoformat()


def send_preview_safe(p, st):
    try:
        send_preview(p, st)
    except Exception as e:  # 사진을 못 보내도 승인은 할 수 있게 글만이라도 보낸다
        print(scrub(f"미리보기 사진 전송 실패 {p['id']}: {e}"))
        msg = say("(사진을 불러오지 못했습니다)\n" + preview_text(p, st), reply_markup=buttons(p["id"], st["status"]))
        st["preview_msg"] = msg["message_id"]
        st["preview_sent"] = now().isoformat()


def list_text(posts, state):
    lines = ["📋 예정된 게시물"]
    for p in posts:
        st = state["posts"].get(p["id"], {"status": "pending"})
        if st["status"] == "published":
            continue
        lines.append(f"{p['id']}  {when(p)}  {p['title']}  — {STATUS_LINE[st['status']].split(' · ')[0]}")
    if len(lines) == 1:
        lines.append("남은 게시물이 없습니다.")
    lines.append("\n/next 다음 게시물 미리보기 받기")
    return "\n".join(lines)


HELP = ("비스트로미 인스타 봇입니다.\n"
        "· 게시 24시간 전에 미리보기와 [✅ 올리기] [⏸ 보류] 버튼을 보내드립니다.\n"
        "· 올리기를 누른 게시물만 예정 시각에 인스타에 올라갑니다.\n"
        "· 버튼을 누른 뒤 반영까지 최대 10분 정도 걸립니다.\n\n"
        "/list 예정된 게시물 보기\n/next 다음 게시물 미리보기 받기")


def handle_updates(state, posts):
    by_id = {p["id"]: p for p in posts}
    offset = state.get("tg_offset", 0)
    for u in tg("getUpdates", offset=offset + 1 if offset else 0, timeout=0, allowed_updates=["message", "callback_query"]):
        state["tg_offset"] = u["update_id"]
        if "callback_query" in u:
            cq = u["callback_query"]
            if str((cq.get("message") or {}).get("chat", {}).get("id", cq["from"]["id"])) != CHAT:
                continue
            action, _, pid = cq.get("data", "").partition(":")
            p, st = by_id.get(pid), state["posts"].get(pid)
            if not p or not st:
                tg_safe("answerCallbackQuery", callback_query_id=cq["id"], text="지금 일정에 없는 게시물입니다.")
                continue
            if st["status"] in ("published", "failed"):
                tg_safe("answerCallbackQuery", callback_query_id=cq["id"], text="이미 처리된 게시물입니다.")
                continue
            st["status"] = "approved" if action == "ok" else "held"
            tg_safe("answerCallbackQuery", callback_query_id=cq["id"],
                    text="승인했습니다." if action == "ok" else "보류했습니다.")
            refresh_preview(p, st)
        elif "message" in u:
            m = u["message"]
            if str(m["chat"]["id"]) != CHAT:
                continue
            cmd = (m.get("text") or "").strip().split()[0].lower() if m.get("text") else ""
            if cmd == "/list":
                say(list_text(posts, state))
            elif cmd == "/next":
                nxt = next((p for p in posts if state["posts"].get(p["id"], {}).get("status", "pending") == "pending"
                            and not state["posts"].get(p["id"], {}).get("preview_msg")), None)
                if nxt:
                    send_preview_safe(nxt, state["posts"].setdefault(nxt["id"], {"status": "pending"}))
                else:
                    say("미리보기를 아직 받지 않은 게시물이 없습니다. /list 로 확인하세요.")
            elif cmd in ("/start", "/help"):
                say(HELP)


# ---------------------------------------------------------------- instagram

def wait_ready(cid):
    for _ in range(40):
        s = ig("GET", cid, fields="status_code").get("status_code")
        if s == "FINISHED":
            return
        if s in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"인스타가 사진 처리에 실패했습니다 ({s})")
        time.sleep(3)
    raise RuntimeError("인스타 사진 처리가 2분 넘게 끝나지 않았습니다")


def find_existing(p):
    """같은 캡션이 이미 올라가 있으면 중복 게시하지 않는다."""
    head = p["caption"].strip()[:80]
    for m in ig("GET", f"{ig_user_id()}/media", fields="id,caption,permalink", limit=10).get("data", []):
        if (m.get("caption") or "").strip()[:80] == head:
            return m


def prepare(p):
    """인스타가 사진을 가져가 게시 준비를 마친 컨테이너 ID를 돌려준다 (아직 게시 전)."""
    uid, urls = ig_user_id(), [RAW + i for i in p["images"]]
    if len(urls) == 1:
        cid = ig("POST", f"{uid}/media", image_url=urls[0], caption=p["caption"])["id"]
    else:
        kids = [ig("POST", f"{uid}/media", image_url=u, is_carousel_item="true")["id"] for u in urls]
        for k in kids:
            wait_ready(k)
        cid = ig("POST", f"{uid}/media", media_type="CAROUSEL", children=",".join(kids), caption=p["caption"])["id"]
    wait_ready(cid)
    return cid


def publish(p):
    found = find_existing(p)
    if found:
        return found["id"], found.get("permalink", "")
    cid = prepare(p)
    mid = ig("POST", f"{ig_user_id()}/media_publish", creation_id=cid)["id"]
    link = ig("GET", mid, fields="permalink").get("permalink", "")
    return mid, link


# ---------------------------------------------------------------- main

def check():
    me = tg("getMe")
    print(f"텔레그램 봇: @{me.get('username')}")
    info = ig("GET", "me", fields="user_id,username")
    print(f"인스타 계정: @{info.get('username')} ({info.get('user_id')})")
    say(f"🔗 연결 확인 완료\n텔레그램 봇 @{me.get('username')}\n인스타 @{info.get('username')}\n\n" + HELP)


def main():
    global TOKEN, CHAT
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
        print("텔레그램 설정이 아직 없습니다 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID). 아무것도 하지 않습니다.")
        return
    CHAT = str(os.environ["TELEGRAM_CHAT_ID"])
    posts = sorted(json.load(open(POSTS_FILE, encoding="utf-8"))["posts"], key=lambda p: p["publish_at"])
    state = json.load(open(STATE_FILE, encoding="utf-8")) if os.path.exists(STATE_FILE) else {}
    state.setdefault("posts", {})
    TOKEN = load_token(state)
    mask(TOKEN)

    try:
        if mode == "check":
            check()
            return
        if mode == "dry-publish":  # 게시 직전 단계까지만 시험하고 실제로 올리지는 않는다
            p = next(p for p in posts if state["posts"].get(p["id"], {}).get("status") != "published")
            prepare(p)
            msg = f"🧪 게시 시험 통과: '{p['title']}' 사진 {len(p['images'])}장과 캡션을 인스타가 받아 게시 준비까지 마쳤습니다. 실제로 올리지는 않았습니다."
            print(msg)
            say(msg)
            return
        handle_updates(state, posts)
        if mode == "preview-next":
            nxt = next((p for p in posts if not state["posts"].get(p["id"], {}).get("preview_msg")), None)
            if nxt:
                send_preview_safe(nxt, state["posts"].setdefault(nxt["id"], {"status": "pending"}))
        t = now()
        for p in posts:
            st = state["posts"].setdefault(p["id"], {"status": "pending"})
            at = datetime.fromisoformat(p["publish_at"])
            if st["status"] in ("published", "failed"):
                continue
            if not st.get("preview_msg") and t >= at - PREVIEW_LEAD:
                send_preview_safe(p, st)
            elif (st["status"] == "pending" and not st.get("reminded") and t >= at - REMIND_LEAD
                  and t - datetime.fromisoformat(st.get("preview_sent", "2000-01-01T00:00:00+09:00")) >= timedelta(hours=1)):
                say(f"⏰ {when(p)} 게시 예정인 '{p['title']}'을(를) 아직 승인하지 않으셨어요. 위 미리보기에서 버튼을 눌러 주세요.",
                    reply_parameters={"message_id": st["preview_msg"], "allow_sending_without_reply": True})
                st["reminded"] = True
            if st["status"] == "approved" and t >= at:
                if not TOKEN:  # 10분마다 같은 경고가 가지 않도록 한 번만 알린다
                    if not state.get("no_token_warned"):
                        say("⚠️ 인스타 연결이 아직 안 돼서 승인된 게시물을 올리지 못하고 있습니다. 연결되면 바로 올라갑니다.")
                        state["no_token_warned"] = True
                    break
                state.pop("no_token_warned", None)
                try:
                    st["media_id"], st["permalink"] = publish(p)
                    st["status"], st["published_at"] = "published", now().isoformat()
                    st.pop("error", None)
                    refresh_preview(p, st)
                    say(f"🎉 게시 완료: {p['title']}\n{st['permalink']}")
                except Exception as e:
                    st["tries"] = st.get("tries", 0) + 1
                    st["error"] = scrub(e)[:300]
                    if st["tries"] >= MAX_TRIES:
                        st["status"] = "failed"
                        refresh_preview(p, st)
                        say(f"⚠️ '{p['title']}' 게시에 {MAX_TRIES}번 실패해서 멈췄습니다.\n오류: {st['error']}")
                    else:
                        say(f"⚠️ '{p['title']}' 게시 실패 ({st['tries']}/{MAX_TRIES}), 10분 뒤 다시 시도합니다.\n오류: {st['error']}")
        if TOKEN:
            refresh_token(state)
    finally:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")


if __name__ == "__main__":
    main()
