"""
German Learning System — Session Engine
Run this at the start of each session to get current progress and recommendations.
Usage: python3 session_engine.py [command]
Commands:
    status          - Full progress overview
    next            - What to teach next
    review          - Topics due for review today
    search [query]  - Search grammar/vocab by keyword
    log_session     - Record a completed session
    update_topic    - Update topic progress
    weakness        - Show current weaknesses
"""
import sqlite3
import json
import sys
import os
from datetime import datetime, timedelta

# Dynamically resolve paths relative to this script's location
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(_SCRIPT_DIR, "german_learning.db")

def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

# ─── STATUS ───

def show_status():
    conn = get_conn()
    c = conn.cursor()

    # Overall counts
    c.execute("SELECT COUNT(*) FROM grammar_topics")
    total_topics = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM vocabulary")
    total_vocab = c.fetchone()[0]

    # Progress by status
    c.execute("""
        SELECT tp.status, COUNT(*) as cnt
        FROM topic_progress tp
        GROUP BY tp.status ORDER BY cnt DESC
    """)
    status_counts = {r['status']: r['cnt'] for r in c.fetchall()}

    # Progress by level
    c.execute("""
        SELECT gt.level_code,
            SUM(CASE WHEN tp.status='mastered' THEN 1 ELSE 0 END) as mastered,
            SUM(CASE WHEN tp.status IN ('practicing','reviewing') THEN 1 ELSE 0 END) as in_progress,
            SUM(CASE WHEN tp.status IN ('not_started','introduced') THEN 1 ELSE 0 END) as remaining,
            COUNT(*) as total
        FROM grammar_topics gt
        JOIN topic_progress tp ON gt.id = tp.topic_id
        GROUP BY gt.level_code
        ORDER BY gt.level_code
    """)
    level_progress = c.fetchall()

    # Recent sessions
    c.execute("SELECT * FROM sessions ORDER BY session_date DESC LIMIT 10")
    recent = c.fetchall()

    # Vocab progress
    c.execute("""
        SELECT status, COUNT(*) as cnt FROM vocab_progress
        GROUP BY status
    """)
    vocab_progress = {r['status']: r['cnt'] for r in c.fetchall()}

    # Active weaknesses
    c.execute("SELECT * FROM weaknesses WHERE resolved_date IS NULL ORDER BY severity DESC")
    weaknesses = c.fetchall()

    print("=" * 60)
    print("  GERMAN LEARNING SYSTEM — PROGRESS DASHBOARD")
    print("=" * 60)
    print(f"\n📚 Curriculum: {total_topics} grammar topics | {total_vocab} vocabulary words")
    print(f"\n📊 Topic Progress:")
    for status, cnt in status_counts.items():
        bar = "█" * cnt + "░" * (total_topics - cnt)
        print(f"   {status:15s}: {cnt:2d}/{total_topics} {bar[:30]}")

    print(f"\n📈 By Level:")
    for lp in level_progress:
        pct = (lp['mastered'] / lp['total'] * 100) if lp['total'] > 0 else 0
        print(f"   {lp['level_code']}: {lp['mastered']}/{lp['total']} mastered ({pct:.0f}%) | {lp['in_progress']} in progress | {lp['remaining']} remaining")

    if vocab_progress:
        print(f"\n📝 Vocabulary: {vocab_progress}")

    if recent:
        print(f"\n🗓️  Last {len(recent)} Sessions:")
        for s in recent:
            score = f"{s['exercises_correct']}/{s['exercises_attempted']}" if s['exercises_attempted'] else "—"
            print(f"   {s['session_date']} | {s['duration_minutes'] or '?'}min | score: {score} | {s['notes'] or ''}")

    if weaknesses:
        print(f"\n⚠️  Active Weaknesses:")
        for w in weaknesses:
            print(f"   [{w['severity']}] {w['description']}")

    conn.close()

# ─── NEXT TOPIC ───

def show_next():
    conn = get_conn()
    c = conn.cursor()

    # Priority 1: Topics due for review today
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("""
        SELECT gt.*, tp.status, tp.confidence_score, tp.next_review_date, tp.times_practiced
        FROM grammar_topics gt
        JOIN topic_progress tp ON gt.id = tp.topic_id
        WHERE tp.next_review_date <= ? AND tp.status NOT IN ('not_started', 'mastered')
        ORDER BY tp.confidence_score ASC
        LIMIT 3
    """, (today,))
    reviews = c.fetchall()

    # Priority 2: Next new topic (in curriculum order)
    c.execute("""
        SELECT gt.*, tp.status
        FROM grammar_topics gt
        JOIN topic_progress tp ON gt.id = tp.topic_id
        WHERE tp.status = 'not_started'
        ORDER BY
            CASE gt.level_code WHEN 'A1' THEN 1 WHEN 'A2' THEN 2 WHEN 'B1' THEN 3 END,
            gt.sort_order
        LIMIT 1
    """)
    next_new = c.fetchone()

    # Priority 3: Topics with lowest confidence
    c.execute("""
        SELECT gt.*, tp.status, tp.confidence_score, tp.weak_areas
        FROM grammar_topics gt
        JOIN topic_progress tp ON gt.id = tp.topic_id
        WHERE tp.status IN ('introduced', 'practicing')
        ORDER BY tp.confidence_score ASC
        LIMIT 3
    """)
    weak_topics = c.fetchall()

    print("=" * 60)
    print("  TODAY'S RECOMMENDED LESSON PLAN")
    print("=" * 60)

    if reviews:
        print(f"\n🔄 REVIEW DUE ({len(reviews)} topics):")
        for r in reviews:
            print(f"   • [{r['level_code']}] {r['topic_name']} (confidence: {r['confidence_score']:.0%}, practiced {r['times_practiced']}x)")
            print(f"     Pattern: {r['pattern_summary']}")

    if weak_topics:
        print(f"\n💪 NEEDS PRACTICE:")
        for w in weak_topics:
            print(f"   • [{w['level_code']}] {w['topic_name']} (confidence: {w['confidence_score']:.0%})")
            if w['weak_areas']:
                print(f"     Weak areas: {w['weak_areas']}")

    if next_new:
        print(f"\n🆕 NEXT NEW TOPIC:")
        print(f"   [{next_new['level_code']}] {next_new['topic_name']}")
        print(f"   {next_new['description']}")
        print(f"   Pattern: {next_new['pattern_summary']}")

    if not reviews and not weak_topics and not next_new:
        print("\n🎉 All topics mastered! You're ready for B1!")

    conn.close()

# ─── REVIEW DUE ───

def show_review():
    conn = get_conn()
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("""
        SELECT gt.*, tp.status, tp.confidence_score, tp.next_review_date, tp.times_practiced
        FROM grammar_topics gt
        JOIN topic_progress tp ON gt.id = tp.topic_id
        WHERE tp.next_review_date <= ? AND tp.status NOT IN ('not_started', 'mastered')
        ORDER BY tp.next_review_date ASC
    """, (today,))
    reviews = c.fetchall()

    if reviews:
        print(f"🔄 {len(reviews)} topics due for review:")
        for r in reviews:
            examples = json.loads(r['examples_json']) if r['examples_json'] else []
            print(f"\n   [{r['level_code']}] {r['topic_name']}")
            print(f"   Status: {r['status']} | Confidence: {r['confidence_score']:.0%} | Practiced: {r['times_practiced']}x")
            print(f"   Pattern: {r['pattern_summary']}")
            if examples:
                print(f"   Example: {examples[0]}")
    else:
        print("✅ No reviews due today!")
    conn.close()

# ─── SEARCH ───

def search(query):
    conn = get_conn()
    c = conn.cursor()

    print(f"🔍 Searching for: '{query}'")

    # Search grammar topics
    c.execute("""
        SELECT gt.*, tp.status, tp.confidence_score
        FROM grammar_fts fts
        JOIN grammar_topics gt ON fts.rowid = gt.id
        LEFT JOIN topic_progress tp ON gt.id = tp.topic_id
        WHERE grammar_fts MATCH ?
        LIMIT 10
    """, (query,))
    grammar_results = c.fetchall()

    # Search vocabulary
    c.execute("""
        SELECT v.*
        FROM vocab_fts fts
        JOIN vocabulary v ON fts.rowid = v.id
        WHERE vocab_fts MATCH ?
        LIMIT 10
    """, (query,))
    vocab_results = c.fetchall()

    if grammar_results:
        print(f"\n📖 Grammar Topics ({len(grammar_results)}):")
        for r in grammar_results:
            print(f"   [{r['level_code']}] {r['topic_name']} — {r['status'] or 'not tracked'}")
            print(f"   {r['pattern_summary']}")

    if vocab_results:
        print(f"\n📝 Vocabulary ({len(vocab_results)}):")
        for r in vocab_results:
            gender = f"({r['gender']}) " if r['gender'] else ""
            print(f"   {gender}{r['german']} = {r['english']}")
            if r['example_sentence']:
                print(f"   → {r['example_sentence']}")

    if not grammar_results and not vocab_results:
        print("   No results found.")

    conn.close()

# ─── LOG SESSION ───

def log_session_interactive():
    """Called from within Claude to log a session."""
    conn = get_conn()
    c = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Check if session already exists today
    c.execute("SELECT id FROM sessions WHERE session_date = ?", (today,))
    existing = c.fetchone()
    if existing:
        print(f"ℹ️  Session already logged for {today} (id: {existing['id']}). Use update commands instead.")
        conn.close()
        return

    c.execute("""INSERT INTO sessions (session_date, started_at) VALUES (?, ?)""", (today, now))
    session_id = c.lastrowid
    conn.commit()
    conn.close()
    print(f"✅ Session {session_id} started for {today}")
    return session_id

# ─── UPDATE TOPIC PROGRESS ───

def update_topic(topic_id, status=None, confidence=None, weak_areas=None):
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d")

    updates = []
    params = []

    if status:
        updates.append("status = ?")
        params.append(status)
    if confidence is not None:
        updates.append("confidence_score = ?")
        params.append(confidence)
    if weak_areas is not None:
        updates.append("weak_areas = ?")
        params.append(weak_areas)

    updates.append("last_practiced = ?")
    params.append(now)
    updates.append("times_practiced = times_practiced + 1")

    # Spaced repetition: next review based on confidence
    if confidence is not None:
        if confidence >= 0.9:
            days = 14
        elif confidence >= 0.7:
            days = 7
        elif confidence >= 0.5:
            days = 3
        else:
            days = 1
        next_review = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        updates.append("next_review_date = ?")
        params.append(next_review)

    # Set first_seen if not set
    updates.append("first_seen = COALESCE(first_seen, ?)")
    params.append(now)

    params.append(topic_id)
    sql = f"UPDATE topic_progress SET {', '.join(updates)} WHERE topic_id = ?"
    c.execute(sql, params)
    conn.commit()

    c.execute("""SELECT gt.topic_name, tp.* FROM topic_progress tp
        JOIN grammar_topics gt ON gt.id = tp.topic_id WHERE tp.topic_id = ?""", (topic_id,))
    result = c.fetchone()
    print(f"✅ Updated: {result['topic_name']} → status={result['status']}, confidence={result['confidence_score']:.0%}, next review={result['next_review_date']}")
    conn.close()

# ─── COMPLETE SESSION ───

def complete_session(session_id, duration, topics_covered, exercises_attempted, exercises_correct, notes, mood):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""UPDATE sessions SET
        duration_minutes=?, topics_covered=?, exercises_attempted=?,
        exercises_correct=?, notes=?, mood=?
        WHERE id=?""",
        (duration, topics_covered, exercises_attempted, exercises_correct, notes, mood, session_id))
    conn.commit()
    conn.close()
    print(f"✅ Session {session_id} completed: {duration}min, {exercises_correct}/{exercises_attempted} correct, mood={mood}")

# ─── SHOW TOPIC DETAIL ───

def show_topic(topic_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT gt.*, tp.status, tp.confidence_score, tp.times_practiced, tp.weak_areas, tp.next_review_date
        FROM grammar_topics gt
        LEFT JOIN topic_progress tp ON gt.id = tp.topic_id
        WHERE gt.id = ?""", (topic_id,))
    t = c.fetchone()
    if not t:
        print(f"Topic {topic_id} not found.")
        return

    examples = json.loads(t['examples_json']) if t['examples_json'] else []
    rules = json.loads(t['rules_json']) if t['rules_json'] else []

    print(f"\n{'='*60}")
    print(f"  [{t['level_code']}] {t['topic_name']}")
    print(f"{'='*60}")
    print(f"\n📝 {t['description']}")
    print(f"\n🔑 Pattern: {t['pattern_summary']}")
    print(f"\n📖 Rules:")
    for r in rules:
        print(f"   • {r}")
    print(f"\n💡 Examples:")
    for e in examples:
        print(f"   → {e}")
    print(f"\n🧠 Tips: {t['tips']}")
    print(f"\n📊 Progress: {t['status']} | Confidence: {t['confidence_score']:.0%} | Practiced: {t['times_practiced']}x")
    if t['weak_areas']:
        print(f"⚠️  Weak areas: {t['weak_areas']}")
    conn.close()

# ─── WEAKNESSES ───

def show_weaknesses():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT w.*, gt.topic_name, gt.level_code
        FROM weaknesses w
        LEFT JOIN grammar_topics gt ON w.related_topic_id = gt.id
        WHERE w.resolved_date IS NULL
        ORDER BY
            CASE w.severity WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 END
    """)
    weaknesses = c.fetchall()

    if weaknesses:
        print(f"⚠️  Active Weaknesses ({len(weaknesses)}):")
        for w in weaknesses:
            topic = f"[{w['level_code']}] {w['topic_name']}" if w['topic_name'] else "General"
            print(f"   [{w['severity'].upper()}] {w['description']} — {topic}")
            if w['notes']:
                print(f"           Notes: {w['notes']}")
    else:
        print("✅ No active weaknesses recorded!")
    conn.close()

# ─── CLI ───

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "status":
        show_status()
    elif cmd == "next":
        show_next()
    elif cmd == "review":
        show_review()
    elif cmd == "search" and len(sys.argv) > 2:
        search(" ".join(sys.argv[2:]))
    elif cmd == "topic" and len(sys.argv) > 2:
        show_topic(int(sys.argv[2]))
    elif cmd == "log_session":
        log_session_interactive()
    elif cmd == "weakness":
        show_weaknesses()
    else:
        print(__doc__)
