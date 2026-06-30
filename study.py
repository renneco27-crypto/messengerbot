"""
study.py — Lightweight study system for CathBot
"""

import os, json, random, re
from typing import Any

STUDY_DIR = "study_data"
os.makedirs(STUDY_DIR, exist_ok=True)

_bot: Any = None

def init_study(bot_instance):
    global _bot
    _bot = bot_instance


def _require_init():
    if _bot is None:
        raise RuntimeError("study.py: init_study(bot) must be called before use")

# ─── JSON I/O ────────────────────────────────────────────────────────────────

def _path(title: str) -> str:
    return os.path.join(STUDY_DIR, f"{title.lower().strip()}.json")

def load_study(title: str) -> dict:
    p = _path(title)
    if not os.path.exists(p):
        return _blank()
    with open(p) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return _blank()

def save_study(title: str, data: dict):
    with open(_path(title), "w") as f:
        json.dump(data, f, indent=2)

def _blank() -> dict:
    return {
        "knowledge": [],
        "enumerations": [],
        "progress": {
            "flashcard_index": 0,
            "history": [],
            "correct": 0,
            "wrong": 0,
            "enumeration_window": 0,
        }
    }

def list_docs() -> list:
    if not os.path.exists(STUDY_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(STUDY_DIR) if f.endswith(".json"))



# ─── Quiz state ──────────────────────────────────────────────────────────────

QUIZ_STATE = {}  # chat_id → {type, answer, title}

def _set_quiz(chat_id, qtype, answer, title):
    QUIZ_STATE[chat_id] = {"type": qtype, "answer": answer, "title": title}

# ─── History helpers ─────────────────────────────────────────────────────────

def _mark_used(data: dict, subject: str):
    h = data["progress"]["history"]
    if subject not in h:
        h.append(subject)

def _pick_unused(data: dict):
    """Returns a random unused knowledge entry, or None if there's no
    knowledge at all. Callers MUST check for None before using the result."""
    subjects = [k["subject"] for k in data["knowledge"]]
    if not subjects:
        return None
    used = set(data["progress"]["history"])
    if all(s in used for s in subjects):
        data["progress"]["history"] = []
        used = set()
    pool = [k for k in data["knowledge"] if k["subject"] not in used]
    return random.choice(pool) if pool else None

# ─── MarkdownV2 helpers ──────────────────────────────────────────────────────

def _e(text: str) -> str:
    """Escape all MarkdownV2 special characters."""
    text = str(text).replace("\\", "\\\\")
    for ch in '_*[]()~`>#+-=|{}.!':
        text = text.replace(ch, '\\' + ch)
    return text

def _trunc(text: str, n: int = 200) -> str:
    return text[:n] + "…" if len(text) > n else text

# ─── Flashcards ──────────────────────────────────────────────────────────────

def cmd_flashcard(message, title: str, reverse: bool = False):
    data = load_study(title)
    entries = data["knowledge"]
    if not entries:
        _bot.reply_to(message, f"📚 No knowledge for '{title}'. Upload and save notes first.")
        return

    idx = data["progress"]["flashcard_index"] % len(entries)
    batch = entries[idx: idx + 20]
    if not batch:
        idx = 0
        batch = entries[:20]

    data["progress"]["flashcard_index"] = (idx + len(batch)) % len(entries)
    save_study(title, data)

    chat_id = message.chat.id

    header = (
        f"📇 *Flashcards — {_e(title)}* \\({idx+1}–{idx+len(batch)} of {len(entries)}\\)\n"
        "_Tap a spoiler to reveal the answer\\._"
    )
    _bot.send_message(chat_id, header, parse_mode="MarkdownV2")

    for i, e in enumerate(batch, idx + 1):
        q = e["description"] if reverse else e["subject"]
        a = e["subject"]    if reverse else e["description"]
        card = f"*{i}\\. {_e(q)}*\n||{_e(_trunc(a))}||"
        _bot.send_message(chat_id, card, parse_mode="MarkdownV2")


# ─── Multiple Choice ─────────────────────────────────────────────────────────

def cmd_mc(message, title: str):
    data = load_study(title)
    if len(data["knowledge"]) < 4:
        _bot.reply_to(message, f"⚠️ Need at least 4 entries for MCQ (have {len(data['knowledge'])}).")
        return

    entry = _pick_unused(data)
    if not entry:
        _bot.reply_to(message, "✅ All entries used! History cleared. Try again.")
        return

    pool = [k for k in data["knowledge"] if k["subject"] != entry["subject"]]
    same = [k for k in pool
            if k.get("chapter") == entry.get("chapter") or k.get("type") == entry.get("type")]
    src  = same if len(same) >= 3 else pool
    if len(src) < 3:
        _bot.reply_to(message, "⚠️ Not enough entries for distractors.")
        return
    distractors = random.sample(src, 3)

    choices = [entry["subject"]] + [d["subject"] for d in distractors]
    random.shuffle(choices)
    correct = "ABCD"[choices.index(entry["subject"])]

    opts = "\n".join(f"{l}\\. {_e(c)}" for l, c in zip("ABCD", choices))
    _mark_used(data, entry["subject"])
    save_study(title, data)
    _set_quiz(message.chat.id, "mc", correct, title)

    _bot.reply_to(
        message,
        f"❓ *Multiple Choice*\n\n{_e(_trunc(entry['description'], 300))}\n\n{opts}\n\n_Reply /answer A, B, C, or D_",
        parse_mode="MarkdownV2"
    )

# ─── Identification ──────────────────────────────────────────────────────────

def cmd_identify(message, title: str):
    data = load_study(title)
    if not data["knowledge"]:
        _bot.reply_to(message, f"📚 No knowledge for '{title}'.")
        return

    entry = _pick_unused(data)
    if not entry:
        _bot.reply_to(message, "✅ All entries used! History cleared. Try again.")
        return

    desc = re.sub(re.escape(entry["subject"]), "__________", entry["description"], flags=re.IGNORECASE)
    if desc == entry["description"]:
        desc = "__________ — " + entry["description"]

    _mark_used(data, entry["subject"])
    save_study(title, data)
    _set_quiz(message.chat.id, "identify", entry["subject"].lower(), title)

    _bot.reply_to(
        message,
        f"🔍 *Identification*\n\n{_e(_trunc(desc, 300))}\n\n_Reply /answer \\[your answer\\]_",
        parse_mode="MarkdownV2"
    )

# ─── True / False ────────────────────────────────────────────────────────────

def cmd_tf(message, title: str):
    data = load_study(title)
    if not data["knowledge"]:
        _bot.reply_to(message, f"📚 No knowledge for '{title}'.")
        return

    entry = _pick_unused(data)
    if not entry:
        _bot.reply_to(message, "✅ All entries used! History cleared. Try again.")
        return

    if random.random() < 0.5:
        stmt    = f"{entry['subject']} — {entry['description']}"
        correct = "true"
    else:
        others = [k for k in data["knowledge"] if k["subject"] != entry["subject"]]
        if others:
            swap    = random.choice(others)
            stmt    = f"{entry['subject']} — {swap['description']}"
            correct = "false"
        else:
            stmt    = f"{entry['subject']} — {entry['description']}"
            correct = "true"

    _mark_used(data, entry["subject"])
    save_study(title, data)
    _set_quiz(message.chat.id, "tf", correct, title)

    _bot.reply_to(
        message,
        f"✅❌ *True or False*\n\n_{_e(_trunc(stmt, 300))}_\n\n_Reply /answer true or /answer false_",
        parse_mode="MarkdownV2"
    )

# ─── Enumeration ─────────────────────────────────────────────────────────────

def cmd_enum(message, title: str):
    data  = load_study(title)
    enums = data["enumerations"]
    if not enums:
        _bot.reply_to(message, f"📚 No enumerations found for '{title}'.")
        return

    WINDOW = 5
    win = data["progress"]["enumeration_window"]
    if win >= len(enums):
        win = 0

    chunk = enums[win: win + WINDOW]
    lines = [
        f"📋 *Enumeration — {_e(title)}*",
        "_Tap each spoiler to reveal the items\\._\n"
    ]
    for en in chunk:
        items_text = ", ".join(_e(i) for i in en["items"])
        lines.append(f"*{_e(en['title'])}*")
        lines.append(f"||{items_text}||\n")

    data["progress"]["enumeration_window"] = win + len(chunk)
    if data["progress"]["enumeration_window"] >= len(enums):
        data["progress"]["enumeration_window"] = 0

    save_study(title, data)
    _bot.reply_to(message, "\n".join(lines), parse_mode="MarkdownV2")

# ─── Search ──────────────────────────────────────────────────────────────────

def cmd_search(message, title: str, query: str):
    data = load_study(title)
    if not data["knowledge"]:
        _bot.reply_to(message, f"📚 No knowledge for '{title}'.")
        return

    q    = query.lower().strip()
    hits = [
        e for e in data["knowledge"]
        if q in e["subject"].lower()
        or q in e.get("description", "").lower()
        or any(q in kw.lower() for kw in e.get("keywords", []))
    ]

    if not hits:
        _bot.reply_to(
            message,
            f"🔍 No results for *{_e(query)}* in *{_e(title)}*\\.",
            parse_mode="MarkdownV2"
        )
        return

    lines = [f"🔍 *{_e(query)}* in *{_e(title)}* — {len(hits)} result\\(s\\)\n"]
    for h in hits[:10]:
        lines.append(f"• *{_e(h['subject'])}* \\[{_e(h.get('type', ''))}\\]")
        lines.append(f"  {_e(_trunc(h['description'], 120))}\n")

    _bot.reply_to(message, "\n".join(lines), parse_mode="MarkdownV2")

# ─── Answer checker ──────────────────────────────────────────────────────────

def cmd_answer(message, answer_text: str):
    cid   = message.chat.id
    state = QUIZ_STATE.get(cid)
    if not state:
        _bot.reply_to(message, "⚠️ No active quiz. Start one with /mc, /identify, or /tf.")
        return

    data    = load_study(state["title"])
    correct = state["answer"].lower().strip()
    given   = answer_text.lower().strip()
    qtype   = state["type"]

    if qtype == "mc":
        ok = given == correct
    elif qtype == "identify":
        ok = (given == correct) or (correct in given) or (given in correct)
    elif qtype == "tf":
        ok = bool(given) and given[0] == correct[0]   # t==t or f==f
    else:
        ok = given == correct

    p = data["progress"]
    if ok:
        p["correct"] += 1
        feedback = f"✅ *Correct\\!*"
    else:
        p["wrong"] += 1
        feedback = f"❌ *Wrong\\!* Answer: *{_e(state['answer'])}*"

    save_study(state["title"], data)
    QUIZ_STATE.pop(cid, None)

    total = p["correct"] + p["wrong"]
    pct   = int(p["correct"] / total * 100) if total else 0
    _bot.reply_to(
        message,
        f"{feedback}\n\n📊 Score: {p['correct']}/{total} \\({pct}%\\)",
        parse_mode="MarkdownV2"
    )

# ─── Stats and doc listing ───────────────────────────────────────────────────

def cmd_studystats(message, title: str):
    data  = load_study(title)
    p     = data["progress"]
    total = p["correct"] + p["wrong"]
    pct   = int(p["correct"] / total * 100) if total else 0

    _bot.reply_to(
        message,
        f"📊 *Stats — {_e(title)}*\n\n"
        f"• Knowledge entries: {len(data['knowledge'])}\n"
        f"• Enumerations: {len(data['enumerations'])}\n"
        f"• Correct answers: {p['correct']}\n"
        f"• Wrong answers: {p['wrong']}\n"
        f"• Score: {pct}%\n"
        f"• Questions used: {len(p['history'])}",
        parse_mode="MarkdownV2"
    )

def cmd_studydocs(message):
    docs = list_docs()
    if not docs:
        _bot.reply_to(
            message,
            "📚 No study documents yet\\.\n"
            "Upload notes with /upload \\[title\\], then /note\\.",
            parse_mode="MarkdownV2"
        )
        return

    lines = [f"• {_e(d)}" for d in docs]
    _bot.reply_to(
        message,
        "📚 *Study Documents*\n\n" + "\n".join(lines) +
        "\n\n*Commands:*\n"
        "/flashcard \\[title\\] — review cards\n"
        "/flashcard reverse \\[title\\] — reversed cards\n"
        "/mc \\[title\\] — multiple choice\n"
        "/identify \\[title\\] — fill in the blank\n"
        "/tf \\[title\\] — true or false\n"
        "/enum \\[title\\] — enumerations\n"
        "/search \\[title\\] \\[query\\] — keyword search\n"
        "/studystats \\[title\\] — your progress",
        parse_mode="MarkdownV2"
    )