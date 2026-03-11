"""Generate an HTML progress dashboard for the German Learning System."""
import sqlite3, json, os
from datetime import datetime

# Dynamically resolve paths relative to this script's location
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(_SCRIPT_DIR, "german_learning.db")
OUT = os.path.join(_SCRIPT_DIR, "dashboard.html")


def build_session_prompt(c, topics, sessions, weaknesses, today):
    """Build the prompt that gets copied to clipboard for the next session."""

    # ── Recent session summaries (last 5) ──
    session_lines = []
    for s in sessions[:5]:
        score = f"{s['exercises_correct']}/{s['exercises_attempted']}" if s['exercises_attempted'] else "no exercises"
        mood = s['mood'] or '?'
        notes = s['notes'] or 'no notes'
        session_lines.append(f"  - {s['session_date']}: {s['duration_minutes'] or '?'}min, score={score}, mood={mood}, notes=\"{notes}\"")
    session_block = "\n".join(session_lines) if session_lines else "  (No sessions yet — this is the first lesson!)"

    # ── Topics due for review ──
    review_lines = []
    for t in topics:
        if t['next_review_date'] and t['next_review_date'] <= today and t['status'] not in ('not_started', 'mastered'):
            review_lines.append(f"  - [id={t['topic_id']}] [{t['level_code']}] {t['topic_name']} (confidence={t['confidence_score']:.0%}, practiced {t['times_practiced']}x)")
    review_block = "\n".join(review_lines) if review_lines else "  (None due today)"

    # ── Next new topic ──
    next_new = None
    for t in topics:
        if t['status'] == 'not_started':
            next_new = t
            break
    if next_new:
        next_block = f"  [id={next_new['topic_id']}] [{next_new['level_code']}] {next_new['topic_name']}\n  \"{next_new['description']}\""
    else:
        next_block = "  All topics have been introduced! Focus on review and mastery."

    # ── Weakest topics (practicing/introduced, lowest confidence) ──
    weak_topics = sorted(
        [t for t in topics if t['status'] in ('introduced', 'practicing') and (t['confidence_score'] or 0) < 0.7],
        key=lambda t: t['confidence_score'] or 0
    )[:3]
    weak_lines = []
    for t in weak_topics:
        weak_lines.append(f"  - [id={t['topic_id']}] [{t['level_code']}] {t['topic_name']} (confidence={t['confidence_score']:.0%})")
    weak_block = "\n".join(weak_lines) if weak_lines else "  (No weak topics yet)"

    # ── Active weaknesses from weaknesses table ──
    weakness_lines = []
    for w in weaknesses:
        weakness_lines.append(f"  - [{w['severity']}] {w['description']}")
    weakness_block = "\n".join(weakness_lines) if weakness_lines else "  (None recorded)"

    # ── Overall progress summary ──
    total = len(topics)
    mastered = sum(1 for t in topics if t['status'] == 'mastered')
    practicing = sum(1 for t in topics if t['status'] in ('practicing', 'reviewing'))
    introduced = sum(1 for t in topics if t['status'] == 'introduced')
    not_started_count = sum(1 for t in topics if t['status'] == 'not_started')

    prompt = f"""Let's do our German lesson today. Please start by reading my learning system instructions and then run my session engine to begin.

STEP 1: Read the instructions file:
  Read file: {_SCRIPT_DIR}/GERMAN_TEACHER_INSTRUCTIONS.md

STEP 2: Run these commands to load my current state:
  python3 "{_SCRIPT_DIR}/session_engine.py" next
  python3 "{_SCRIPT_DIR}/session_engine.py" review

STEP 3: Here is my current progress snapshot (auto-generated on {today}):

OVERALL: {mastered}/{total} mastered, {practicing} in progress, {introduced} introduced, {not_started_count} not started

RECENT SESSIONS:
{session_block}

REVIEWS DUE TODAY:
{review_block}

WEAKEST TOPICS:
{weak_block}

NEXT NEW TOPIC:
{next_block}

ACTIVE WEAKNESSES:
{weakness_block}

STEP 4: Based on the above, run today's 30-minute lesson following the format in the instructions file (5 min review → 20 min teach/practice → 5 min wrap-up). At the end of the session, update my database and regenerate the dashboard:
  python3 "{_SCRIPT_DIR}/generate_dashboard.py"
"""
    return prompt


def generate():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # ── Gather data ──
    c.execute("""
        SELECT gt.level_code, gt.topic_name, gt.sort_order, gt.id as topic_id,
            gt.description, gt.pattern_summary,
            tp.status, tp.confidence_score, tp.times_practiced, tp.next_review_date
        FROM grammar_topics gt
        JOIN topic_progress tp ON gt.id = tp.topic_id
        ORDER BY CASE gt.level_code WHEN 'A1' THEN 1 WHEN 'A2' THEN 2 WHEN 'B1' THEN 3 END, gt.sort_order
    """)
    topics = c.fetchall()

    c.execute("SELECT * FROM sessions ORDER BY session_date DESC LIMIT 10")
    sessions = c.fetchall()

    c.execute("SELECT * FROM weaknesses WHERE resolved_date IS NULL ORDER BY severity DESC")
    weaknesses = c.fetchall()

    c.execute("SELECT COUNT(*) as cnt FROM vocabulary")
    vocab_count = c.fetchone()['cnt']

    # ── Stats ──
    total = len(topics)
    mastered = sum(1 for t in topics if t['status'] == 'mastered')
    practicing = sum(1 for t in topics if t['status'] in ('practicing', 'reviewing'))
    introduced = sum(1 for t in topics if t['status'] == 'introduced')
    not_started = sum(1 for t in topics if t['status'] == 'not_started')
    overall_pct = (mastered / total * 100) if total else 0

    today = datetime.now().strftime("%Y-%m-%d")
    reviews_due = sum(1 for t in topics if t['next_review_date'] and t['next_review_date'] <= today and t['status'] not in ('not_started', 'mastered'))

    # ── Build the next-session prompt ──
    session_prompt = build_session_prompt(c, topics, sessions, weaknesses, today)
    # Escaped version for HTML display (inside <div>)
    session_prompt_escaped = session_prompt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # JSON-safe version for JavaScript variable
    session_prompt_js = json.dumps(session_prompt)

    total_sessions = len(sessions)
    total_exercises = sum(s['exercises_attempted'] or 0 for s in sessions)
    total_correct = sum(s['exercises_correct'] or 0 for s in sessions)
    accuracy = (total_correct / total_exercises * 100) if total_exercises else 0

    # ── Group topics by level ──
    levels_data = {}
    for t in topics:
        lv = t['level_code']
        if lv not in levels_data:
            levels_data[lv] = []
        levels_data[lv].append(t)

    # ── Build level sections ──
    level_meta = {
        'A1': {'label': 'A1 — Beginner', 'color': '#10b981', 'accent': '#059669', 'icon': '🌱', 'desc': 'Foundations: articles, cases, word order, basic verbs'},
        'A2': {'label': 'A2 — Elementary', 'color': '#3b82f6', 'accent': '#2563eb', 'icon': '📘', 'desc': 'Building blocks: past tense, dative, modals, clauses'},
        'B1': {'label': 'B1 — Intermediate', 'color': '#8b5cf6', 'accent': '#7c3aed', 'icon': '🎯', 'desc': 'Exam-ready: passive, subjunctive, complex structures'},
    }

    status_info = {
        'not_started': {'color': '#475569', 'bg': '#1e293b', 'label': 'Not started', 'icon': '○'},
        'introduced':  {'color': '#3b82f6', 'bg': '#1e3a5f', 'label': 'Introduced',  'icon': '◐'},
        'practicing':  {'color': '#f59e0b', 'bg': '#422006', 'label': 'Practicing',   'icon': '◑'},
        'reviewing':   {'color': '#a78bfa', 'bg': '#2e1065', 'label': 'Reviewing',    'icon': '◕'},
        'mastered':    {'color': '#10b981', 'bg': '#064e3b', 'label': 'Mastered',      'icon': '●'},
    }

    grammar_html = ""
    for lv_code in ['A1', 'A2', 'B1']:
        meta = level_meta[lv_code]
        lv_topics = levels_data.get(lv_code, [])
        lv_mastered = sum(1 for t in lv_topics if t['status'] == 'mastered')
        lv_total = len(lv_topics)
        lv_pct = (lv_mastered / lv_total * 100) if lv_total else 0

        # Topic cards
        cards_html = ""
        for i, t in enumerate(lv_topics):
            st = status_info.get(t['status'], status_info['not_started'])
            conf = t['confidence_score'] or 0
            conf_pct = conf * 100
            conf_display = f"{conf_pct:.0f}%" if conf > 0 else "—"
            practiced = t['times_practiced'] or 0
            review = t['next_review_date'] or ""
            review_display = ""
            if review and t['status'] not in ('not_started', 'mastered'):
                if review <= today:
                    review_display = '<span class="review-badge due">Review due</span>'
                else:
                    review_display = f'<span class="review-badge">Next: {review}</span>'

            # Confidence bar color
            if conf >= 0.7:
                bar_color = "#10b981"
            elif conf >= 0.4:
                bar_color = "#f59e0b"
            elif conf > 0:
                bar_color = "#ef4444"
            else:
                bar_color = "#334155"

            cards_html += f'''
            <div class="topic-card" style="border-left-color:{st['color']}">
                <div class="topic-header">
                    <span class="topic-num">{i+1}</span>
                    <span class="topic-name">{t['topic_name']}</span>
                    <span class="status-pill" style="background:{st['bg']};color:{st['color']}">{st['icon']} {st['label']}</span>
                </div>
                <div class="topic-desc">{t['description']}</div>
                <div class="topic-meta">
                    <div class="confidence-section">
                        <span class="meta-label">Confidence</span>
                        <div class="confidence-bar-wrap">
                            <div class="confidence-bar" style="width:{conf_pct}%;background:{bar_color}"></div>
                        </div>
                        <span class="meta-value">{conf_display}</span>
                    </div>
                    <div class="practiced-section">
                        <span class="meta-label">Practiced</span>
                        <span class="meta-value">{practiced}x</span>
                    </div>
                    {review_display}
                </div>
            </div>'''

        grammar_html += f'''
        <div class="level-section">
            <div class="level-header" style="border-color:{meta['color']}">
                <div class="level-title">
                    <span class="level-icon">{meta['icon']}</span>
                    <div>
                        <h3 style="color:{meta['color']}">{meta['label']}</h3>
                        <p class="level-desc">{meta['desc']}</p>
                    </div>
                </div>
                <div class="level-stats">
                    <div class="level-progress-ring">
                        <svg viewBox="0 0 36 36" class="progress-ring">
                            <path class="ring-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                            <path class="ring-fill" stroke="{meta['color']}" stroke-dasharray="{lv_pct}, 100" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"/>
                        </svg>
                        <span class="ring-text">{lv_mastered}/{lv_total}</span>
                    </div>
                </div>
            </div>
            <div class="topic-list">
                {cards_html}
            </div>
        </div>'''

    # ── Session rows ──
    session_rows = ""
    for s in sessions:
        score = f"{s['exercises_correct']}/{s['exercises_attempted']}" if s['exercises_attempted'] else "—"
        mood_emoji = {'easy': '<span title="Easy">😊</span>', 'ok': '<span title="OK">😐</span>', 'hard': '<span title="Hard">😓</span>'}.get(s['mood'] or '', '—')
        session_rows += f'''<tr>
            <td>{s['session_date']}</td>
            <td class="center">{s['duration_minutes'] or '?'} min</td>
            <td class="center">{score}</td>
            <td class="center">{mood_emoji}</td>
            <td class="muted">{s['notes'] or ''}</td>
        </tr>\n'''
    if not session_rows:
        session_rows = '<tr><td colspan="5" class="empty-msg">No sessions recorded yet — start your first lesson!</td></tr>'

    # ── Weakness cards ──
    weakness_html = ""
    for w in weaknesses:
        sev_color = {'high': '#ef4444', 'medium': '#f59e0b', 'low': '#3b82f6'}.get(w['severity'], '#6b7280')
        weakness_html += f'''<div class="weakness-card" style="border-left-color:{sev_color}">
            <span class="weakness-severity" style="color:{sev_color}">{w['severity'].upper()}</span>
            <span class="weakness-text">{w['description']}</span>
        </div>\n'''
    if not weakness_html:
        weakness_html = '<div class="empty-msg" style="padding:20px">No weaknesses recorded yet.</div>'

    # ── Status legend ──
    legend_items = ""
    for key in ['not_started', 'introduced', 'practicing', 'reviewing', 'mastered']:
        si = status_info[key]
        legend_items += f'<span class="legend-item"><span class="legend-dot" style="background:{si["color"]}"></span>{si["label"]}</span>'

    # ── Full HTML ──
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>German B1 Progress</title>
<style>
:root {{
    --bg-deep: #0a0f1a;
    --bg-card: #0f172a;
    --bg-surface: #1e293b;
    --text-primary: #f1f5f9;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;
    --border: #1e293b;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: var(--bg-deep); color: var(--text-primary); line-height: 1.6; }}
.container {{ max-width: 960px; margin: 0 auto; padding: 32px 24px; }}

/* Header */
.header {{ margin-bottom: 32px; }}
.header h1 {{ font-size: 26px; font-weight: 700; display: flex; align-items: center; gap: 10px; }}
.header .subtitle {{ color: var(--text-muted); font-size: 14px; margin-top: 4px; }}

/* Stat cards */
.stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 28px; }}
@media (max-width: 600px) {{ .stats {{ grid-template-columns: repeat(2, 1fr); }} }}
.stat {{ background: var(--bg-card); border-radius: 12px; padding: 18px 16px; border: 1px solid var(--border); }}
.stat .value {{ font-size: 28px; font-weight: 700; }}
.stat .label {{ font-size: 12px; color: var(--text-muted); margin-top: 2px; text-transform: uppercase; letter-spacing: 0.5px; }}

/* Overall progress bar */
.overall-bar {{ background: var(--bg-surface); border-radius: 10px; height: 20px; overflow: hidden; margin-bottom: 6px; }}
.overall-fill {{ height: 100%; border-radius: 10px; background: linear-gradient(90deg, #10b981, #3b82f6, #8b5cf6); transition: width 0.6s ease; min-width: 2px; }}
.overall-label {{ text-align: center; font-size: 12px; color: var(--text-muted); margin-bottom: 32px; }}

/* Section headings */
.section-title {{ font-size: 18px; font-weight: 600; margin: 36px 0 16px; display: flex; align-items: center; gap: 8px; }}

/* Level sections */
.level-section {{ margin-bottom: 28px; }}
.level-header {{
    display: flex; justify-content: space-between; align-items: center;
    background: var(--bg-card); border-radius: 14px; padding: 18px 20px;
    border: 1px solid var(--border); margin-bottom: 2px;
    border-left: 4px solid;
}}
.level-title {{ display: flex; align-items: center; gap: 12px; }}
.level-icon {{ font-size: 28px; }}
.level-title h3 {{ font-size: 16px; font-weight: 700; margin: 0; }}
.level-desc {{ font-size: 13px; color: var(--text-muted); margin-top: 2px; }}
.level-stats {{ flex-shrink: 0; }}

/* Circular progress */
.level-progress-ring {{ position: relative; width: 52px; height: 52px; }}
.progress-ring {{ transform: rotate(-90deg); width: 52px; height: 52px; }}
.ring-bg {{ fill: none; stroke: var(--bg-surface); stroke-width: 3; }}
.ring-fill {{ fill: none; stroke-width: 3; stroke-linecap: round; transition: stroke-dasharray 0.6s ease; }}
.ring-text {{ position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; color: var(--text-primary); }}

/* Topic cards */
.topic-list {{ display: flex; flex-direction: column; gap: 2px; }}
.topic-card {{
    background: var(--bg-card); padding: 14px 18px; border-left: 3px solid;
    border-bottom: 1px solid var(--border);
    transition: background 0.15s;
}}
.topic-card:last-child {{ border-radius: 0 0 14px 14px; border-bottom: none; }}
.topic-card:hover {{ background: #131c2e; }}
.topic-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 4px; flex-wrap: wrap; }}
.topic-num {{ font-size: 11px; font-weight: 700; color: var(--text-muted); background: var(--bg-surface); width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }}
.topic-name {{ font-size: 14px; font-weight: 600; flex: 1; min-width: 200px; }}
.status-pill {{ font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 20px; white-space: nowrap; letter-spacing: 0.2px; }}
.topic-desc {{ font-size: 12px; color: var(--text-muted); margin-left: 32px; margin-bottom: 8px; }}
.topic-meta {{ display: flex; align-items: center; gap: 20px; margin-left: 32px; flex-wrap: wrap; }}
.meta-label {{ font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.4px; }}
.meta-value {{ font-size: 13px; font-weight: 600; }}
.confidence-section {{ display: flex; align-items: center; gap: 8px; }}
.confidence-bar-wrap {{ width: 80px; height: 6px; background: var(--bg-surface); border-radius: 3px; overflow: hidden; }}
.confidence-bar {{ height: 100%; border-radius: 3px; transition: width 0.4s ease; min-width: 1px; }}
.practiced-section {{ display: flex; align-items: center; gap: 6px; }}
.review-badge {{ font-size: 11px; color: var(--text-muted); background: var(--bg-surface); padding: 2px 8px; border-radius: 10px; }}
.review-badge.due {{ color: #fbbf24; background: #422006; font-weight: 600; }}

/* Legend */
.legend {{ display: flex; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }}
.legend-item {{ display: flex; align-items: center; gap: 5px; font-size: 12px; color: var(--text-secondary); }}
.legend-dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}

/* Tables */
table {{ width: 100%; border-collapse: collapse; background: var(--bg-card); border-radius: 12px; overflow: hidden; border: 1px solid var(--border); }}
th {{ background: var(--bg-surface); padding: 10px 16px; text-align: left; font-size: 12px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }}
td {{ padding: 10px 16px; border-bottom: 1px solid var(--border); font-size: 14px; }}
tr:last-child td {{ border-bottom: none; }}
.center {{ text-align: center; }}
.muted {{ color: var(--text-muted); font-size: 13px; }}
.empty-msg {{ color: var(--text-muted); text-align: center; padding: 24px; font-size: 14px; }}

/* Weakness cards */
.weakness-list {{ display: flex; flex-direction: column; gap: 6px; }}
.weakness-card {{ background: var(--bg-card); padding: 12px 16px; border-left: 3px solid; border-radius: 0 10px 10px 0; display: flex; align-items: center; gap: 10px; border: 1px solid var(--border); border-left-width: 3px; }}
.weakness-severity {{ font-size: 10px; font-weight: 700; letter-spacing: 0.5px; flex-shrink: 0; }}
.weakness-text {{ font-size: 14px; }}

/* Start session CTA */
.session-cta {{
    background: linear-gradient(135deg, #0f172a 0%, #1a1f3a 100%);
    border: 1px solid #2d3a6a;
    border-radius: 16px;
    padding: 24px 28px;
    margin-bottom: 32px;
    position: relative;
    overflow: hidden;
}}
.session-cta::before {{
    content: '';
    position: absolute; top: 0; left: 0; right: 0; height: 3px;
    background: linear-gradient(90deg, #10b981, #3b82f6, #8b5cf6);
}}
.cta-top {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 12px; }}
.cta-title {{ font-size: 18px; font-weight: 700; }}
.cta-title span {{ color: var(--text-muted); font-weight: 400; font-size: 14px; margin-left: 8px; }}
.copy-btn {{
    display: inline-flex; align-items: center; gap: 8px;
    background: linear-gradient(135deg, #10b981, #059669);
    color: white; border: none; padding: 12px 24px;
    border-radius: 10px; font-size: 15px; font-weight: 600;
    cursor: pointer; transition: all 0.2s; letter-spacing: 0.2px;
}}
.copy-btn:hover {{ transform: translateY(-1px); box-shadow: 0 4px 20px rgba(16, 185, 129, 0.3); }}
.copy-btn:active {{ transform: translateY(0); }}
.copy-btn.copied {{
    background: linear-gradient(135deg, #3b82f6, #2563eb);
}}
.copy-btn svg {{ width: 18px; height: 18px; }}
.cta-preview {{
    background: var(--bg-deep); border: 1px solid var(--border);
    border-radius: 10px; padding: 14px 16px;
    font-family: 'SF Mono', Menlo, Consolas, monospace;
    font-size: 12px; color: var(--text-secondary); line-height: 1.7;
    max-height: 0; overflow: hidden; transition: max-height 0.4s ease, padding 0.4s ease, margin 0.4s ease;
    white-space: pre-wrap; word-break: break-word;
    margin-top: 0; padding: 0 16px;
}}
.cta-preview.expanded {{
    max-height: 600px; overflow-y: auto; padding: 14px 16px; margin-top: 12px;
}}
.toggle-preview {{
    background: none; border: none; color: var(--text-muted);
    font-size: 12px; cursor: pointer; padding: 4px 0; margin-top: 8px;
    text-decoration: underline; text-underline-offset: 2px;
}}
.toggle-preview:hover {{ color: var(--text-secondary); }}
</style>
</head>
<body>
<div class="container">

<div class="header">
    <h1>🇩🇪 German B1 Journey</h1>
    <div class="subtitle">Last updated {datetime.now().strftime("%B %d, %Y at %H:%M")} &middot; Goal: Goethe B1 Exam &middot; Passport</div>
</div>

<div class="session-cta">
    <div class="cta-top">
        <div class="cta-title">Start Next Session<span>Copy and paste into Claude</span></div>
        <button class="copy-btn" onclick="copyPrompt()" id="copyBtn">
            <svg id="copyIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
            <svg id="checkIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display:none"><polyline points="20 6 9 17 4 12"/></svg>
            <span id="btnText">Copy Session Prompt</span>
        </button>
    </div>
    <button class="toggle-preview" onclick="togglePreview()" id="toggleBtn">Show prompt preview</button>
    <div class="cta-preview" id="promptPreview">{session_prompt_escaped}</div>
</div>

<div class="stats">
    <div class="stat"><div class="value" style="color:#10b981">{overall_pct:.0f}%</div><div class="label">Overall Progress</div></div>
    <div class="stat"><div class="value">{mastered}<span style="font-size:16px;color:var(--text-muted)">/{total}</span></div><div class="label">Topics Mastered</div></div>
    <div class="stat"><div class="value" style="color:{('#fbbf24' if reviews_due > 0 else 'var(--text-primary)')}">{reviews_due}</div><div class="label">Reviews Due</div></div>
    <div class="stat"><div class="value">{vocab_count}</div><div class="label">Vocabulary</div></div>
    <div class="stat"><div class="value">{total_sessions}</div><div class="label">Sessions</div></div>
    <div class="stat"><div class="value" style="color:#a78bfa">{accuracy:.0f}%</div><div class="label">Accuracy</div></div>
</div>

<div class="overall-bar"><div class="overall-fill" style="width:{max(overall_pct, 0.5)}%"></div></div>
<div class="overall-label">{mastered}/{total} topics mastered &middot; {practicing} in progress &middot; {not_started} remaining</div>

<div class="section-title">Grammar Curriculum</div>
<div class="legend">{legend_items}</div>

{grammar_html}

<div class="section-title">Recent Sessions</div>
<table>
<thead><tr><th>Date</th><th class="center">Duration</th><th class="center">Score</th><th class="center">Mood</th><th>Notes</th></tr></thead>
<tbody>{session_rows}</tbody>
</table>

<div class="section-title">Active Weaknesses</div>
<div class="weakness-list">{weakness_html}</div>

</div>
<script>
const sessionPrompt = {session_prompt_js};

function copyPrompt() {{
    navigator.clipboard.writeText(sessionPrompt).then(() => {{
        const btn = document.getElementById('copyBtn');
        const copyIcon = document.getElementById('copyIcon');
        const checkIcon = document.getElementById('checkIcon');
        const btnText = document.getElementById('btnText');
        btn.classList.add('copied');
        copyIcon.style.display = 'none';
        checkIcon.style.display = 'block';
        btnText.textContent = 'Copied!';
        setTimeout(() => {{
            btn.classList.remove('copied');
            copyIcon.style.display = 'block';
            checkIcon.style.display = 'none';
            btnText.textContent = 'Copy Session Prompt';
        }}, 2500);
    }}).catch(() => {{
        // Fallback for older browsers
        const ta = document.createElement('textarea');
        ta.value = sessionPrompt;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        document.getElementById('btnText').textContent = 'Copied!';
        setTimeout(() => {{ document.getElementById('btnText').textContent = 'Copy Session Prompt'; }}, 2500);
    }});
}}

function togglePreview() {{
    const preview = document.getElementById('promptPreview');
    const btn = document.getElementById('toggleBtn');
    preview.classList.toggle('expanded');
    btn.textContent = preview.classList.contains('expanded') ? 'Hide prompt preview' : 'Show prompt preview';
}}
</script>
</body>
</html>"""

    with open(OUT, 'w') as f:
        f.write(html)
    print(f"✅ Dashboard generated: {OUT}")
    conn.close()

if __name__ == "__main__":
    generate()
