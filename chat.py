import json, os, sys, time, datetime, urllib.request
from zoneinfo import ZoneInfo
KEY = os.environ["OPENROUTER_API_KEY"]; TG = os.environ["TELEGRAM_BOT_TOKEN"]
DAILY_CAP = 15
MODELS = {"manager": ["nvidia/nemotron-3-ultra-550b-a55b:free", "google/gemma-4-31b-it:free", "baidu/ernie-5.5-flash-preview:free"],
          "qa": ["nvidia/nemotron-3-ultra-550b-a55b:free", "google/gemma-4-31b-it:free", "nvidia/nemotron-3.5-super-vl:free"],
          "content": ["nvidia/nemotron-3-ultra-550b-a55b:free", "google/gemma-4-31b-it:free", "baidu/ernie-5.5-flash-preview:free"]}
DEFAULT = ["google/gemma-4-31b-it:free", "baidu/ernie-5.5-flash-preview:free", "nvidia/nemotron-3.5-super-vl:free", "inclusionai/ling-3.2-flash:free"]
os.makedirs("chatcache", exist_ok=True)
SF = "chatcache/chat_state.json"
S = json.load(open(SF)) if os.path.exists(SF) else {}
S.setdefault("offset", 0); S.setdefault("agent", None); S.setdefault("hist", {}); S.setdefault("day", ""); S.setdefault("n", 0)
today = datetime.datetime.now(ZoneInfo("Africa/Cairo")).strftime("%Y-%m-%d")
if S["day"] != today: S["day"] = today; S["n"] = 0
cfg = json.load(open("agents.json"))
PEOPLE = {"manager": cfg["manager"], "qa": cfg["qa"]}
for a in cfg["agents"]: PEOPLE[a["id"]] = a
for k, a in cfg.get("on_demand", {}).items(): PEOPLE[k] = a
CID = open("state/chat_id.txt").read().strip()
status = json.load(open("state/status.json")).get("agents", {}) if os.path.exists("state/status.json") else {}
mem = open("state/memory.md", encoding="utf-8").read()[-1500:] if os.path.exists("state/memory.md") else ""
news = open("state/news_input.md", encoding="utf-8").read()[-1200:] if os.path.exists("state/news_input.md") else ""
def tg(method, **p):
    req = urllib.request.Request(f"https://api.telegram.org/bot{TG}/{method}", json.dumps(p).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r: return json.load(r)
def send(t): tg("sendMessage", chat_id=CID, text=t[:3800])
def model_reply(aid, msgs):
    p = PEOPLE[aid]; st = status.get(aid, {})
    system = (f"You are {p['person']}, {p['title']} at {cfg['company']}\nYour job: {p['role']}\n"
      f"You are chatting directly with the founder (the boss) on Telegram. Stay in character as {p['person']}. Reply in Egyptian Arabic (عامية مصرية) unless he writes in English. Be short, warm and concrete (2 to 6 sentences).\n"
      "Never invent facts, numbers, results or work you did not do. Only describe your work from the context below; if you do not know or have not done something, say so plainly and offer what you can do in your next run. You are an AI employee: never claim to have done things outside your own work, browsed the web, or contacted anyone.\n\n"
      f"Your latest recorded work: state={st.get('state','no run yet')}, task={st.get('task','-')}, model={st.get('model','-')}, output preview: {st.get('preview','-')}\n"
      f"Today's news input (the only facts you may use about the news; if it is empty, say no news was fed in today):\n{news or '(nothing fed in today)'}\n\n"
      f"Recent company memory:\n{mem or '(none)'}")
    last = None
    for m in MODELS.get(aid, DEFAULT) + ["openrouter/free"]:
        try:
            body = json.dumps({"model": m, "messages": [{"role": "system", "content": system}] + msgs, "max_tokens": 700}).encode()
            req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", body, {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=100) as r: t = json.load(r)["choices"][0]["message"]["content"]
            if t and t.strip(): return t.strip(), m
        except Exception as e:
            last = str(e)[:60]; print("model", m, "failed:", last, file=sys.stderr); time.sleep(8 if "429" in last else 2)
    return None, last
ups = tg("getUpdates", offset=S["offset"], timeout=0).get("result", [])
if not os.path.exists(SF):   # first ever run: skip old backlog so nothing stale is answered
    S["offset"] = (ups[-1]["update_id"] + 1) if ups else 0
    json.dump(S, open(SF, "w")); print("initialized, skipped", len(ups)); sys.exit(0)
for u in ups[:8]:
    S["offset"] = u["update_id"] + 1
    m = u.get("message") or {}
    if str((m.get("chat") or {}).get("id")) != CID: continue   # only the founder's chat
    txt = (m.get("text") or "").strip()
    if not txt: continue
    if txt.startswith("/start"):
        arg = txt.split(" ", 1)[1].strip() if " " in txt else ""
        if arg.startswith("talk_") and arg[5:] in PEOPLE: S["agent"] = arg[5:]; txt = "(The founder just opened a direct chat with you. Greet him briefly in character and ask what he wants.)"
        else: continue
    elif txt.startswith("/exit") or txt.startswith("/bye"):
        old = S["agent"]; S["agent"] = None
        send(f"Left the chat with {PEOPLE[old]['person']}." if old else "No active chat."); continue
    if not S["agent"]:
        send("No active chat. Tap an employee in the office to start one: https://rawaj-agents-ops.github.io/rawaj-office/"); continue
    if S["n"] >= DAILY_CAP:
        send(f"Daily chat budget used ({DAILY_CAP} replies) to protect the free model quota. Chat resumes tomorrow after midnight Cairo time."); continue
    aid = S["agent"]; h = S["hist"].setdefault(aid, []); h.append({"role": "user", "content": txt})
    reply, info = model_reply(aid, h[-8:])
    if not reply:
        h.pop(); send(f"{PEOPLE[aid]['person']} could not answer: the free model is unavailable right now ({info}). Nothing was made up. Try again later."); continue
    S["n"] += 1; h.append({"role": "assistant", "content": reply}); S["hist"][aid] = h[-12:]
    send(f"{PEOPLE[aid]['person']}: {reply}\n\n[model: {info.replace(':free','')} | {S['n']}/{DAILY_CAP} today | /exit to leave]")
json.dump(S, open(SF, "w"))
print("processed", len(ups[:8]), "updates; agent", S["agent"], "n", S["n"])
