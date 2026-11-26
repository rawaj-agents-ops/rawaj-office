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

DEAD = {"n": 0}
def call(system, user, aid="research"):
    if DEAD["n"] >= 2 and aid not in ("manager", "qa"):
        print("quota looks exhausted: skipping", aid, file=sys.stderr); return None, None
    for m in PRIORITY.get(aid, PRIORITY["research"]) + [LIGHT, "openrouter/free"]:
        for attempt in range(1):
            try:
                body = json.dumps({"model": m, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_tokens": 1500}).encode()
                req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", body, {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    t = json.load(r)["choices"][0]["message"]["content"]
                if t and t.strip():
                    DEAD["n"] = 0
                    return t.strip(), m
            except Exception as e:
                print("model", m, "failed:", e, file=sys.stderr)
                time.sleep(10 if "429" in str(e) else 3)
                if "401" in str(e) or "402" in str(e): break
    DEAD["n"] += 1
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
            ctx = "\n".join(f"[{k}] {v.get('preview','')}" for k, v in st["agents"].items())
            out, m = call(sysmsg, f"Date: {today}. Midday pass: produce a fresh, different deliverable than your morning one. Stay consistent with the team's latest work:\n{ctx}", a["id"])
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
PERSON = {x["id"]: x.get("person", x["name"]) for x in cfg["agents"] + [cfg["qa"], cfg["manager"]]}
roster = "\n".join(f"- {x['person']}, {x['title']} (id {x['id']}): {x['role']}" for x in cfg["agents"]) + f"\n- {PERSON['qa']}, QA Lead: {cfg['qa']['role']}\n- {PERSON['manager']}, Operations Manager: coordinates the team and writes the founder's report"
MEM_ON = os.path.exists("state/memory_on.txt")
mem = open("state/memory.md").read()[-3000:] if MEM_ON and os.path.exists("state/memory.md") else ""
if mem:
    roster += "\n\nPrevious days (learn from it, do not repeat mistakes or offers):\n" + mem
plan, pm = call(f"You are {PERSON['manager']}, the Manager at {cfg['company']}\nTeam:\n{roster}", f"Date: {today}. Write today's short plan (max 120 words): one concrete focus per department, how their outputs depend on each other, and what to avoid duplicating or contradicting. Plain text.", "manager")
plan = plan or "No plan today. Each agent works from its role and stays consistent with teammates' outputs."
os.makedirs("out", exist_ok=True)
open(f"out/{today}-plan.md", "w").write(plan)
status["manager"] = {"person": PERSON["manager"], "title": cfg["manager"]["title"], "name": "Manager", "state": "working", "task": "daily plan", "model": pm, "at": now.isoformat(), "preview": plan[:140].replace("\n"," ")}
for a in cfg["agents"]:
    sysmsg = (f"You are {a['person']}, the {a['title']} at {cfg['company']}\nYour job: {a['role']}\n"
              f"The whole team:\n{roster}\n\nManager's plan for today:\n{plan}\n\n"
              "Write concisely and concretely. Output only your deliverable. Stay consistent with your teammates' work shown below: reuse their facts, names and offers, do not duplicate or contradict them. If something conflicts or is missing, say so in one line starting with FLAG:")
    done_so_far = "\n\n".join(f"[{k}] {v[:700]}" for k, v in outputs.items() if v) or "(nobody has finished yet, you are first)"
    out, m = call(sysmsg, f"Date: {today}. Produce today's deliverable.\n\nTeammates' work so far today:\n{done_so_far}", a["id"])
    outputs[a["id"]] = out
    status[a["id"]] = {"person": a["person"], "title": a["title"], "name": a["name"], "state": "done" if out else "failed", "task": a["role"][:120], "model": m, "at": now.isoformat(), "preview": (out or "")[:140].replace("\n"," ")}
    print(a["id"], status[a["id"]]["state"])
bundle = "\n\n".join(f"### {k}\n{v}" for k, v in outputs.items() if v)
qa, qm = call(f"You are the QA agent at {cfg['company']}\n{cfg['qa']['role']}", "Review the whole team's work against the manager plan and against each other (consistency, duplicates, contradictions, missing pieces). For each department give its id, then PASS or FIX plus one line.\n\nTeam:\n" + roster + "\n\nManager plan:\n" + plan + "\n\nOutputs:\n\n" + bundle, "qa")
import re
flagged = [x["id"] for x in cfg["agents"] if qa and re.search(re.escape(x["id"]) + r"\W{0,12}FIX", qa, re.I)] if isinstance(cfg.get("agents"), list) else []
status["qa"] = {"flagged": flagged, "person": PERSON["qa"], "title": cfg["qa"]["title"], "name": "QA", "state": "done" if qa else "failed", "task": "review", "model": qm, "at": now.isoformat(), "preview": (qa or "")[:140].replace("\n"," ")}
rep, rm = call(
    f"You are the Manager agent at {cfg['company']}\nWrite the founder's daily report in Egyptian Arabic. Refer to each employee by first name (Omar, Layla, Yousef, Nour, Karim, Hala, Tariq, Mariam) with their role, keeping the names in English letters (عامية مصرية), friendly and short. Structure: 1) one line summary, 2) a line or two per department about what it produced today and the best item, 3) QA verdict, 4) the top 3 actions for the founder today (concrete, start with sending the outreach messages), 5) a section titled محتاجين قرارك listing every line starting with FLAG: in the department outputs or QA as a short question to him (write none if there are no flags), and anything else that needs his decision. Plain text, no markdown symbols. Be honest: say when something failed or is unverified.",
    "Department outputs:\n\n" + bundle + "\n\nQA review:\n" + (qa or "QA failed"), "manager")
status["manager"] = {"person": PERSON["manager"], "title": cfg["manager"]["title"], "name": "Manager", "state": "done" if rep else "failed", "task": "daily report", "model": rm, "at": now.isoformat(), "preview": (rep or "")[:140].replace("\n"," ")}
if MEM_ON:
    line = f"\n## {today}\nQA flagged: {', '.join(flagged) or 'none'}. Summary: {(rep or '')[:350]}\n"
    open("state/memory.md", "a").write(line)
    t = open("state/memory.md").read(); open("state/memory.md", "w").write(t[-3500:])
os.makedirs("out", exist_ok=True)
open(f"out/{today}.md", "w").write(f"# Rawaj daily {today}\n\n## Report\n{rep}\n\n## QA\n{qa}\n\n## Outputs\n{bundle}\n")
json.dump({"updated": now.isoformat(), "agents": status}, open("state/status.json", "w"), ensure_ascii=False, indent=1)
force = os.path.exists("state/force_send.txt")
if not rep:
    print("no manager report today (free quota exhausted?): not sending a fallback message"); sys.exit(0)
if os.path.exists(sent) and not force:
    print("report already sent today: status updated, no second Telegram message"); sys.exit(0)
cid = chat_id()
if not cid:
    print("no chat id yet: user must press Start on the bot"); sys.exit(0)
used = "\n".join(f"- {v.get('person', v['name'])}, {v.get('title', v['name'])}: {(v['model'] or 'فشل').replace(':free','')}" for v in status.values())
text = f"تقرير رواج اليومي - {today}\n\n" + (rep or "النظام اشتغل بس مفيش تقرير طلع النهارده. هراجع المشكلة.") + "\n\nالموديلات المجانية اللي اشتغلوا النهارده:\n" + used
if os.path.exists("state/recruitment.md") and not os.path.exists("state/recruitment_sent"):
    text += "\n\n" + open("state/recruitment.md", encoding="utf-8").read()[:2600]
    open("state/recruitment_sent", "w").write("ok")
for i in range(0, len(text), 3800):
    print("telegram sent:", tg("sendMessage", chat_id=cid, text=text[i:i+3800]).get("ok"))
open(sent, "w").write("ok")
if force:
    os.remove("state/force_send.txt")
