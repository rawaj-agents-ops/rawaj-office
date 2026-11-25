import json, os, sys, time, datetime, urllib.request, urllib.error
from zoneinfo import ZoneInfo
KEY = os.environ["OPENROUTER_API_KEY"]
TG = os.environ["TELEGRAM_BOT_TOKEN"]
ULTRA = "nvidia/nemotron-3-ultra-550b-a55b:free"
GEMMA = "google/gemma-4-31b-it:free"
ERNIE = "baidu/ernie-5.5-flash-preview:free"
SUPER = "nvidia/nemotron-3.5-super-vl:free"
LING = "inclusionai/ling-3.2-flash:free"
G26 = "google/gemma-4-26b-a4b-it:free"
LIGHT = "nvidia/nemotron-3.5-lightning:free"
# strongest scarce quota first for manager, QA and content; plentiful models for the rest. Max 4 tries per agent.
PRIORITY = {
 "manager": [ULTRA, GEMMA, ERNIE, SUPER],
 "qa": [ULTRA, GEMMA, SUPER, ERNIE],
 "content": [ULTRA, GEMMA, ERNIE, SUPER],
 "lead_research": [GEMMA, ULTRA, ERNIE, LING],
 "research": [GEMMA, ERNIE, SUPER, LING],
 "design": [ERNIE, GEMMA, LING, G26],
 "scheduling": [LING, GEMMA, ERNIE, G26],
 "client_success": [SUPER, GEMMA, LING, "openrouter/free"],
}
now = datetime.datetime.now(ZoneInfo("Africa/Cairo"))
today = now.strftime("%Y-%m-%d")
cfg = json.load(open("agents.json"))

def call(system, user, aid="research"):
    for m in PRIORITY.get(aid, PRIORITY["research"]) + [LIGHT, "openrouter/free"]:
        for attempt in range(2):
            try:
                body = json.dumps({"model": m, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_tokens": 1500}).encode()
                req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", body, {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    t = json.load(r)["choices"][0]["message"]["content"]
                if t and t.strip():
                    return t.strip(), m
            except Exception as e:
                print("model", m, "failed:", e, file=sys.stderr)
                time.sleep(20 if "429" in str(e) else 5)
                if "401" in str(e) or "402" in str(e): break
    return None, None

def tg(method, **p):
    req = urllib.request.Request(f"https://api.telegram.org/bot{TG}/{method}", json.dumps(p).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def chat_id():
    f = "state/chat_id.txt"
    if os.path.exists(f):
        return open(f).read().strip()
    for u in tg("getUpdates")["result"]:
        c = (u.get("message") or {}).get("chat")
        if c:
            open(f, "w").write(str(c["id"]))
            return str(c["id"])
    return None

PULSES = {"30 9 * * *": ["research", "lead_research"], "30 12 * * *": ["content", "design"], "0 16 * * *": ["scheduling", "client_success"]}
pulse = PULSES.get(os.environ.get("SCHEDULE_CRON", ""))
if pulse:
    st = json.load(open("state/status.json")) if os.path.exists("state/status.json") else {"agents": {}}
    for a in cfg["agents"]:
        if a["id"] in pulse:
            sysmsg = f"You are the {a['name']} agent at {cfg['company']}\nYour job: {a['role']}\nWrite concisely and concretely. Output only your deliverable."
            out, m = call(sysmsg, f"Date: {today}. Midday pass: produce a fresh, different deliverable than your morning one.", a["id"])
            st["agents"][a["id"]] = {"name": a["name"], "state": "done" if out else "failed", "task": a["role"][:120], "model": m, "at": now.isoformat(), "preview": (out or "")[:140].replace("\n", " ")}
            os.makedirs("out", exist_ok=True)
            open(f"out/{today}-{a['id']}-pulse.md", "w").write(out or "failed")
    st["updated"] = now.isoformat()
    json.dump(st, open("state/status.json", "w"), ensure_ascii=False, indent=1)
    sys.exit(0)
# sent-today guard for scheduled runs
sent = f"state/sent-{today}"
if os.environ.get("EVENT") == "schedule":
    if os.path.exists(sent) or now.hour < 8:
        print("skip: already sent or before 8am Cairo"); sys.exit(0)

status, outputs = {}, {}
for a in cfg["agents"]:
    sysmsg = f"You are the {a['name']} agent at {cfg['company']}\nYour job: {a['role']}\nWrite concisely and concretely. Output only your deliverable."
    out, m = call(sysmsg, f"Date: {today}. Produce today's deliverable.", a["id"])
    outputs[a["id"]] = out
    status[a["id"]] = {"name": a["name"], "state": "done" if out else "failed", "task": a["role"][:120], "model": m, "at": now.isoformat(), "preview": (out or "")[:140].replace("\n"," ")}
    print(a["id"], status[a["id"]]["state"])
bundle = "\n\n".join(f"### {k}\n{v}" for k, v in outputs.items() if v)
qa, qm = call(f"You are the QA agent at {cfg['company']}\n{cfg['qa']['role']}", "Review these outputs. For each department give PASS or FIX plus one line.\n\n" + bundle, "qa")
import re
flagged = [x["id"] for x in cfg["agents"] if qa and re.search(re.escape(x["id"]) + r"\W{0,12}FIX", qa, re.I)] if isinstance(cfg.get("agents"), list) else []
status["qa"] = {"flagged": flagged, "name": "QA", "state": "done" if qa else "failed", "task": "review", "model": qm, "at": now.isoformat(), "preview": (qa or "")[:140].replace("\n"," ")}
rep, rm = call(
    f"You are the Manager agent at {cfg['company']}\nWrite the founder's daily report in Egyptian Arabic (عامية مصرية), friendly and short. Structure: 1) one line summary, 2) a line or two per department about what it produced today and the best item, 3) QA verdict, 4) the top 3 actions for the founder today (concrete, start with sending the outreach messages), 5) anything that needs his decision. Plain text, no markdown symbols. Be honest: say when something failed or is unverified.",
    "Department outputs:\n\n" + bundle + "\n\nQA review:\n" + (qa or "QA failed"), "manager")
status["manager"] = {"name": "Manager", "state": "done" if rep else "failed", "task": "daily report", "model": rm, "at": now.isoformat(), "preview": (rep or "")[:140].replace("\n"," ")}
os.makedirs("out", exist_ok=True)
open(f"out/{today}.md", "w").write(f"# Rawaj daily {today}\n\n## Report\n{rep}\n\n## QA\n{qa}\n\n## Outputs\n{bundle}\n")
json.dump({"updated": now.isoformat(), "agents": status}, open("state/status.json", "w"), ensure_ascii=False, indent=1)
if os.path.exists(sent):
    print("report already sent today: status updated, no second Telegram message"); sys.exit(0)
cid = chat_id()
if not cid:
    print("no chat id yet: user must press Start on the bot"); sys.exit(0)
used = "\n".join(f"- {v['name']}: {(v['model'] or 'فشل').replace(':free','')}" for v in status.values())
text = f"تقرير رواج اليومي - {today}\n\n" + (rep or "النظام اشتغل بس مفيش تقرير طلع النهارده. هراجع المشكلة.") + "\n\nالموديلات المجانية اللي اشتغلوا النهارده:\n" + used
for i in range(0, len(text), 3800):
    print("telegram sent:", tg("sendMessage", chat_id=cid, text=text[i:i+3800]).get("ok"))
open(sent, "w").write("ok")
