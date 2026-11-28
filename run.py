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
}
now = datetime.datetime.now(ZoneInfo("Africa/Cairo"))
today = now.strftime("%Y-%m-%d")
cfg = json.load(open("agents.json"))

NEWSF = "state/news_input.md"
_news_raw = open(NEWSF, encoding="utf-8").read() if os.path.exists(NEWSF) else ""
NEWS = _news_raw.strip()[:6000]
news_ok = bool(NEWS) and (today in _news_raw)
PERF = (open("state/perf.md", encoding="utf-8").read()[-1500:] if os.path.exists("state/perf.md") else "").strip()
NEWS_BLOCK = ("Today's verified news input. These items, with their source URLs, are the ONLY facts the team may use:\n" + NEWS) if news_ok else ("(NO NEWS INPUT FOR TODAY: state/news_input.md is missing, empty, or not dated " + today + ". Nobody may invent news. Every department must output one FLAG line saying there is no news input today.)")

DEAD = {"n": 0}
def call(system, user, aid="research"):
    if DEAD["n"] >= 2 and aid not in ("manager", "qa"):
        print("quota looks exhausted: skipping", aid, file=sys.stderr); return None, None
    for m in PRIORITY.get(aid, PRIORITY["content"]) + [LIGHT, "openrouter/free"]:
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

# no news input: spend no model quota and say so plainly instead of inventing
if not news_ok:
    _msg = ("رواج ستوديو: مفيش أخبار مدخلة النهارده في state/news_input.md، فالفريق مكتبش حاجة من عنده ومفيش فيديو النهارده. "
            "ابعتلي الأخبار بمصادرها وأنا أشغل الفريق.")
    _sent = f"state/sent-{today}"
    if not os.path.exists(_sent):
        try:
            _cid = chat_id()
            if _cid:
                print("no news: telegram sent:", tg("sendMessage", chat_id=_cid, text=_msg).get("ok"))
                open(_sent, "w").write("ok")
        except Exception as e:
            print("could not send the no-news note:", e, file=sys.stderr)
    json.dump({"updated": now.isoformat(), "news": "missing", "agents": {"manager": {"person": cfg["manager"]["person"], "title": cfg["manager"]["title"], "name": "Manager", "state": "skipped", "task": "no news input today", "model": None, "at": now.isoformat(), "preview": "no news input"}}}, open("state/status.json", "w"), ensure_ascii=False, indent=1)
    sys.exit(0)

PULSES = {"30 12 * * *": ["content"]}
pulse = PULSES.get(os.environ.get("SCHEDULE_CRON", ""))
if pulse:
    st = json.load(open("state/status.json")) if os.path.exists("state/status.json") else {"agents": {}}
    for a in cfg["agents"]:
        if a["id"] in pulse:
            sysmsg = f"You are {a['person']}, the {a['title']} at {cfg['company']}\nYour job: {a['role']}\nYou cannot browse the internet. Use only the news items you are given. Never invent facts: write one line starting with FLAG: instead.\nWrite in Egyptian Arabic (عامية مصرية) unless a format needs an English label. Output only your deliverable."
            ctx = "\n".join(f"[{k}] {v.get('preview','')}" for k, v in st["agents"].items())
            out, m = call(sysmsg, f"Date: {today}.\n\n{NEWS_BLOCK}\n\nMidday pass: produce a fresh, different deliverable than your morning one, from the same news items only. Stay consistent with the team's latest work:\n{ctx}", a["id"])
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
roster = "\n".join(f"- {x['person']}, {x['title']} (id {x['id']}): {x['role']}" for x in cfg["agents"]) + f"\n- {PERSON['qa']}, {cfg['qa']['title']}: {cfg['qa']['role']}\n- {PERSON['manager']}, {cfg['manager']['title']}: picks the lead story, coordinates the team and writes the founder's report"
MEM_ON = os.path.exists("state/memory_on.txt")
mem = open("state/memory.md").read()[-3000:] if MEM_ON and os.path.exists("state/memory.md") else ""
if mem:
    roster += "\n\nPrevious days (learn from it, do not repeat mistakes or offers):\n" + mem
plan, pm = call(f"You are {PERSON['manager']}, the {cfg['manager']['title']} at {cfg['company']}\nTeam:\n{roster}\n\nNobody on the team can browse the internet. The only facts allowed today are the news items below.\n\n{NEWS_BLOCK}", f"Date: {today}. Write today's short plan (max 90 words) in Egyptian Arabic: which story leads and why, what Yousef writes and what Mariam checks, and what to avoid. Plain text.", "manager")
plan = plan or "No plan today. Each agent works from its role and stays consistent with teammates' outputs."
os.makedirs("out", exist_ok=True)
open(f"out/{today}-plan.md", "w").write(plan)
status["manager"] = {"person": PERSON["manager"], "title": cfg["manager"]["title"], "name": "Manager", "state": "working", "task": "daily plan", "model": pm, "at": now.isoformat(), "preview": plan[:140].replace("\n"," ")}
for a in cfg["agents"]:
    sysmsg = (f"You are {a['person']}, the {a['title']} at {cfg['company']}\nYour job: {a['role']}\n"
              f"The whole team:\n{roster}\n\nEditor's plan for today:\n{plan}\n\n"
              "Hard rules: you cannot browse the internet, so use only today's news items below. Do not add facts, numbers, dates, names, quotes or product details from your own memory. If you need something that is not in the news items, write one line starting with FLAG: and leave it out. Nothing is published automatically: your output is a draft the founder posts himself.\n"
              "Write in Egyptian Arabic (عامية مصرية) unless a format needs an English label. Write concisely and concretely. Output only your deliverable. Stay consistent with your teammates' work shown below: reuse their facts and story, do not duplicate or contradict them.\n\n"
              + NEWS_BLOCK)
    done_so_far = "\n\n".join(f"[{k}] {v[:700]}" for k, v in outputs.items() if v) or "(nobody has finished yet, you are first)"
    if not news_ok and a["id"] == "research":
        out, m = "FLAG: مفيش أخبار مدخلة النهارده في state/news_input.md، فمش هألف أي خبر. محتاج لستة الأخبار بمصادرها قبل التشغيل.", None
    else:
        out, m = call(sysmsg, f"Date: {today}. Produce today's deliverable from today's news items only.\n\nTeammates' work so far today:\n{done_so_far}", a["id"])
    if PERF and a["id"] in ("client_success", "lead_research", "scheduling"):
        extra, em = call(sysmsg, f"Date: {today}. The founder pasted these performance numbers. Use only these numbers, invent none, and say what to repeat and what to drop:\n{PERF}\n\nTeammates' work so far today:\n{done_so_far}", a["id"])
        if extra:
            out = (out or "") + "\n\nمراجعة الأداء (أرقام المالك فقط):\n" + extra
            m = m or em
    outputs[a["id"]] = out
    status[a["id"]] = {"person": a["person"], "title": a["title"], "name": a["name"], "state": "done" if out else "failed", "task": a["role"][:120], "model": m, "at": now.isoformat(), "preview": (out or "")[:140].replace("\n"," ")}
    print(a["id"], status[a["id"]]["state"])
bundle = "\n\n".join(f"### {k}\n{v}" for k, v in outputs.items() if v)
qa, qm = call(f"You are {PERSON['qa']}, the {cfg['qa']['title']} at {cfg['company']}\n{cfg['qa']['role']}\n\n" + NEWS_BLOCK, "Fact-check the whole team's work against today's news items only: every claim must trace to one of those items and its source. Then check consistency between departments, duplicates, contradictions and missing pieces. For each department give its id, then PASS or FIX plus one line naming the exact claim that does not trace.\n\nTeam:\n" + roster + "\n\nEditor's plan:\n" + plan + "\n\nOutputs:\n\n" + bundle, "qa")
import re
flagged = [x["id"] for x in cfg["agents"] if qa and re.search(re.escape(x["id"]) + r"\W{0,12}FIX", qa, re.I)] if isinstance(cfg.get("agents"), list) else []
status["qa"] = {"flagged": flagged, "person": PERSON["qa"], "title": cfg["qa"]["title"], "name": "QA", "state": "done" if qa else "failed", "task": "review", "model": qm, "at": now.isoformat(), "preview": (qa or "")[:140].replace("\n"," ")}
qa_text = qa if qa else "QA did not run today: the free models failed before the review, so nothing was fact-checked."
attrib = "; ".join(f"{k} = {next((x['person'] for x in cfg['agents'] if x['id']==k), PERSON.get(k, k))}" for k in outputs if outputs[k])
rep, rm = call(
    f"You are {PERSON['manager']}, the {cfg['manager']['title']} at {cfg['company']}\nWrite the founder's daily report in Egyptian Arabic (عامية مصرية), short and honest. Refer to each employee by first name (Omar, Yousef, Mariam) with their role, names in English letters. The team is only these three. The page is a short-video tech and AI news page on Instagram Reels and TikTok, fronted by an AI anchor; the founder posts everything himself, nothing is published automatically.\n"
    "Structure: 1) سطر واحد ملخص للي حصل النهارده، 2) الأخبار اللي اشتغلنا عليها النهارده: كل خبر بسطر ومصدره، 3) السكربت الجاهز: الهوك، ملخص السكربت في ٣ سطور، والكابشن، 4) رأي مریم (QA): PASS ولا FIX وإيه المشكلة، 5) أهم ٣ حاجات يعملها هو النهارده (أولها ينشر الفيديو من حسابه)، 6) قسم اسمه محتاجين قرارك فيه كل سطر FLAG: من الشغل أو من QA كسؤال قصير ليه (اكتب مفيش لو مفيش).\n"
    "Attribute every department output to the person whose id labels it, exactly like this: " + attrib + ". If QA did not run, say plainly that nothing was fact-checked today and do not describe any QA verdict.\n"
    "Plain text, no markdown symbols. Be honest: say when a department failed or when a claim is unverified, and never invent news or numbers. Never promise follower counts or results." + ("\nIf the founder pasted performance numbers today, add a short weekly review: what to repeat and what to drop, using only his numbers." if PERF else ""),
    "Date: " + today + "\n\n" + NEWS_BLOCK + "\n\nDepartment outputs:\n\n" + bundle + "\n\nQA review:\n" + qa_text, "manager")
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
news_line = "أخبار النهارده: " + ("وصلت في state/news_input.md" if news_ok else "مفيش أخبار مدخلة النهارده، فالفريق مكتبش حاجة من عنده")
text = f"تقرير رواج ستوديو اليومي - {today}\n\n" + (rep or "النظام اشتغل بس مفيش تقرير طلع النهارده. هراجع المشكلة.") + "\n\n" + news_line + "\n\nالموديلات المجانية اللي اشتغلوا النهارده:\n" + used
if os.path.exists("state/recruitment.md") and not os.path.exists("state/recruitment_sent"):
    text += "\n\n" + open("state/recruitment.md", encoding="utf-8").read()[:2600]
    open("state/recruitment_sent", "w").write("ok")
for i in range(0, len(text), 3800):
    print("telegram sent:", tg("sendMessage", chat_id=cid, text=text[i:i+3800]).get("ok"))
open(sent, "w").write("ok")
if force:
    os.remove("state/force_send.txt")
