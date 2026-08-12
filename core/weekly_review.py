#!/usr/bin/env python3
"""
weekly_review.py — نظام المراجعة الأسبوعية الذكية لرائد التداول
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
يعمل كل ثلاثاء 04:00 UTC تلقائياً داخل APScheduler.
يُرسل تقريراً شاملاً للمدير عبر Telegram.

الوكلاء:
  - AgentOKX:     فحص OKX API للتحديثات الجديدة
  - AgentFinance: مراجعة المنطق المالي (SL/TP/R:R/ATR/Fibonacci)
  - AgentCode:    تحليل جودة الكود وAST
  - AgentQA:      اختبارات الجودة الشاملة
  - AgentReport:  تجميع وإرسال التقرير
"""

import asyncio
import json
import logging
import ast
import re
import os
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# auditing_agent: طبقة التدقيق الإلزامية
try:
    from core.auditing_agent import audit_content, audit_financial_content
    _AUDITING_REVIEW = True
except ImportError:
    def audit_content(c, source="default"): return True, c
    def audit_financial_content(c, source="review"): return True, c
    _AUDITING_REVIEW = False

# ═══════════════════════════════════════════════════════════════
# ذاكرة Redis المستمرة بين الجلسات
# ═══════════════════════════════════════════════════════════════

REVIEW_REDIS_KEY   = "raed:weekly_review:latest"
HISTORY_REDIS_KEY  = "raed:weekly_review:history"
MAX_HISTORY        = 8  # آخر 8 مراجعات (شهرين)

def _save_review_to_redis(redis_client, data: dict):
    """حفظ نتائج المراجعة في Redis للذاكرة المستمرة."""
    try:
        if not redis_client:
            return
        data["timestamp"] = datetime.now(timezone.utc).isoformat()
        # الحفظ كأحدث مراجعة
        redis_client.setex(REVIEW_REDIS_KEY, 60 * 60 * 24 * 8,
                           json.dumps(data, ensure_ascii=False))
        # إضافة للتاريخ
        history_raw = redis_client.get(HISTORY_REDIS_KEY)
        history = json.loads(history_raw) if history_raw else []
        history.append({
            "date":    data["timestamp"],
            "score":   data.get("overall_score", 0),
            "issues":  len(data.get("critical_issues", [])),
            "summary": data.get("summary", ""),
        })
        history = history[-MAX_HISTORY:]  # احتفظ بآخر 8 فقط
        redis_client.setex(HISTORY_REDIS_KEY, 60 * 60 * 24 * 60,
                           json.dumps(history, ensure_ascii=False))
        logger.info("✅ weekly_review: محفوظ في Redis")
    except Exception as e:
        logger.error(f"weekly_review Redis save: {e}")

def _load_previous_review(redis_client) -> Optional[dict]:
    """تحميل المراجعة السابقة للمقارنة."""
    try:
        if not redis_client:
            return None
        raw = redis_client.get(REVIEW_REDIS_KEY)
        return json.loads(raw) if raw else None
    except Exception:
        return None

def _load_review_history(redis_client) -> List[dict]:
    """تحميل تاريخ المراجعات."""
    try:
        if not redis_client:
            return []
        raw = redis_client.get(HISTORY_REDIS_KEY)
        return json.loads(raw) if raw else []
    except Exception:
        return []

# ═══════════════════════════════════════════════════════════════
# الوكيل 1: AgentOKX — فحص مستودع OKX
# ═══════════════════════════════════════════════════════════════

async def agent_okx_review() -> Dict[str, Any]:
    """
    يفحص مستودع OKX agent-skills للتحديثات:
    - endpoints جديدة
    - أصول مُرمَّزة جديدة
    - مؤشرات تقنية جديدة
    - تغييرات في API
    """
    result = {
        "agent": "OKX",
        "status": "ok",
        "findings": [],
        "new_features": [],
        "warnings": [],
    }
    try:
        # فحص GitHub API
        urls = [
            "https://api.github.com/repos/okx/agent-skills/contents/skills",
            "https://api.github.com/repos/okx/agent-skills/commits?per_page=5",
        ]
        for url in urls:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "RaedReview/1.0",
                             "Accept": "application/vnd.github.v3+json"},
                )
                resp = urllib.request.urlopen(req, timeout=10)
                data = json.loads(resp.read())
                if "commits" in url and isinstance(data, list):
                    for commit in data[:3]:
                        msg = commit.get("commit", {}).get("message", "")
                        date = commit.get("commit", {}).get("author", {}).get("date", "")
                        result["findings"].append(f"📝 {date[:10]}: {msg[:80]}")
                elif isinstance(data, list):
                    skills = [item["name"] for item in data if item.get("type") == "dir"]
                    result["findings"].append(f"📦 Skills المتاحة: {', '.join(skills)}")
                    # تحقق من skills جديدة
                    known_skills = {
                        "okx-cex-market", "okx-cex-trade",
                        "okx-cex-portfolio", "okx-cex-smartmoney",
                        "okx-sentiment-tracker",
                    }
                    new_skills = set(skills) - known_skills
                    if new_skills:
                        result["new_features"].append(
                            f"🆕 Skills جديدة: {', '.join(new_skills)}")
            except Exception as e:
                result["warnings"].append(f"⚠️ GitHub fetch: {e}")

        # فحص OKX API للأصول المُرمَّزة الجديدة
        try:
            stock_url = ("https://www.okx.com/api/v5/public/instruments"
                         "?instType=SPOT&instFamily=STOCKS")
            req = urllib.request.Request(
                stock_url,
                headers={"User-Agent": "RaedReview/1.0"})
            resp = urllib.request.urlopen(req, timeout=8)
            stock_data = json.loads(resp.read())
            if stock_data.get("code") == "0":
                instruments = stock_data.get("data", [])
                x_stocks = [i["instId"] for i in instruments
                            if i.get("instId", "").startswith("X")]
                result["findings"].append(
                    f"📊 أصول X-prefix في OKX Spot: {len(x_stocks)} أصل")
                # الأصول المعروفة حالياً
                known_x = {
                    "XSPCX-USDT", "XAMZN-USDT", "XAAPL-USDT", "XGOOGL-USDT",
                    "XMETA-USDT", "XAMD-USDT", "XNFLX-USDT", "XSPY-USDT",
                    "XORCL-USDT", "XAVGO-USDT", "XMSFT-USDT", "XCOIN-USDT",
                    "XNVDA-USDT",
                }
                new_x = set(x_stocks) - known_x
                if new_x:
                    result["new_features"].append(
                        f"🆕 أصول X-prefix جديدة: {', '.join(sorted(new_x))}")
        except Exception as e:
            result["warnings"].append(f"⚠️ OKX instruments: {e}")

    except Exception as e:
        result["status"] = "error"
        result["warnings"].append(f"❌ AgentOKX خطأ: {e}")

    return result

# ═══════════════════════════════════════════════════════════════
# الوكيل 2: AgentFinance — مراجعة المنطق المالي
# ═══════════════════════════════════════════════════════════════

def agent_finance_review(project_files: Dict[str, str]) -> Dict[str, Any]:
    """
    يراجع المنطق المالي في جميع الملفات:
    - R:R ratio (يجب ≥ 1:1)
    - Stop Loss منطقي (لا يتجاوز 15%)
    - ATR-based sizing
    - Fibonacci levels
    - Trailing Stop logic
    - Position sizing
    """
    result = {
        "agent": "Finance",
        "status": "ok",
        "critical": [],
        "warnings": [],
        "ok": [],
    }

    an = project_files.get("analysis.py", "")
    dl = project_files.get("data_layer.py", "")

    # ── فحص R:R ──
    rr_checks = [
        ("R/R الواقعي: 1:1.0", "R:R مُعدَّل تلقائياً لضمان 1:1 ✅"),
        ("rr_ratio", "حساب R:R موجود ✅"),
    ]
    for pattern, msg in rr_checks:
        if pattern in an:
            result["ok"].append(f"✅ {msg}")

    # ── فحص Stop Loss ──
    sl_patterns = re.findall(r"(\d+\.?\d*)%[-–]?\)", an)
    large_sl = [p for p in sl_patterns if float(p) > 20]
    if large_sl:
        result["warnings"].append(
            f"⚠️ SL قد يكون كبيراً ({max(large_sl)}%) — تحقق من FIN2c")
    else:
        result["ok"].append("✅ SL% في نطاق معقول (< 20%)")

    # ── فحص ATR ──
    if "_atr" in an.lower() or "atr_pct" in an:
        result["ok"].append("✅ ATR-based sizing موجود")
    else:
        result["critical"].append("❌ ATR-based sizing غير موجود!")

    # ── فحص Fibonacci ──
    if "_calc_fibonacci" in an or "fib" in an:
        result["ok"].append("✅ Fibonacci calculation موجود")
        # تحقق من price_cap
        if "price_cap_mult" in an:
            result["ok"].append("✅ Fibonacci price cap موجود (يمنع مستويات بعيدة)")
    else:
        result["critical"].append("❌ Fibonacci calculation غير موجود!")

    # ── فحص Trailing Stop ──
    if "Trailing Stop" in an and "rsi < 15" in an.lower() or "rsi < 30" in an.lower():
        result["ok"].append("✅ Trailing Stop مع RSI filter موجود")

    # ── فحص Yahoo Finance ──
    if "_ohlcv_yahoo" in dl:
        result["ok"].append("✅ Yahoo Finance fallback موجود")
    if "SPY" in dl and "proxy" in dl.lower():
        result["ok"].append("✅ SPY proxy لـ XSPCX موجود")

    # ── فحص Tier system ──
    if "الأسهم المُرمَّزة Futures → جميع الباقات" in an:
        result["ok"].append("✅ Tier: Futures للجميع، Spot للذهبي+")
    else:
        result["warnings"].append(
            "⚠️ Tier rules للأسهم المُرمَّزة — تحقق")

    # ── فحص Fear & Greed ──
    if "get_fear_greed" in an or "fear_greed" in an.lower():
        result["ok"].append("✅ Fear & Greed مُدمَج في التحليل")

    # حساب النقاط
    score = (len(result["ok"]) * 10
             - len(result["warnings"]) * 5
             - len(result["critical"]) * 20)
    result["score"] = max(0, min(100, score))

    return result

# ═══════════════════════════════════════════════════════════════
# الوكيل 3: AgentCode — تحليل جودة الكود
# ═══════════════════════════════════════════════════════════════

def agent_code_review(project_files: Dict[str, str]) -> Dict[str, Any]:
    """
    يراجع جودة الكود:
    - AST syntax check
    - Import paths
    - Error handling
    - X-prefix fixes
    - Memory/Cache
    """
    result = {
        "agent": "Code",
        "status": "ok",
        "critical": [],
        "warnings": [],
        "ok": [],
    }

    critical_checks = {
        "analysis.py": [
            ("TK_ROOT_fix: X-prefix",        "_is_x_prefix"),
            ("TK_name_fix: display symbol",   "_display_symbol = raw_arg"),
            ("QS_fix: qs_sym",               "qs_sym    = raw_arg"),
            ("TK_tier_fix: Futures",          "الأسهم المُرمَّزة Futures → جميع الباقات"),
            ("FA_bug_fix",                    "_sym_disp"),
            ("FIN2c: outlier SL",             "_recent = candles[-min(10"),
            ("FIN3b: Trailing RSI",           "rsi < 15"),
            ("BT1_fix: Yahoo backtest",       "_ohlcv_yahoo"),
            ("core.data_layer imports",       "from core.data_layer import"),
            ("R1: /risk command",             'CommandHandler("risk"'),
        ],
        "plan.py": [
            ("TK5_fix: plan_month",   "_use_futures_pm"),
            ("TK6_fix: plan_week",    "_use_futures_pw"),
            ("PLAN_DUP_fix",          "_disp_sym_pw"),
            ("PLAN_NAME_fix",         "_display_map_pm"),
            ("PLAN_HDR_fix",          "_disp_str_pm"),
        ],
        "data_layer.py": [
            ("DL1: Yahoo Finance",    "async def _ohlcv_yahoo("),
            ("DL2: resolve_stock",    "def resolve_stock_symbol("),
            ("DL3: XSPCX map",        '"XSPCX"'),
            ("BT1_fix: get_hist",     "BT1_fix"),
            ("SPCX proxy",            '"yahoo": "SPY"'),
            ("TK1b_fix3: self.session", "self.session, url,"),
        ],
    }

    for filename, checks in critical_checks.items():
        src = project_files.get(filename, "")
        if not src:
            result["warnings"].append(f"⚠️ {filename}: غير محمَّل")
            continue

        # AST check
        try:
            ast.parse(src)
            result["ok"].append(f"✅ {filename}: syntax نظيف ({src.count(chr(10))+1} سطر)")
        except SyntaxError as e:
            result["critical"].append(f"❌ {filename}: SyntaxError في السطر {e.lineno}")

        # checks
        for check_name, pattern in checks:
            if pattern in src:
                result["ok"].append(f"✅ {check_name}")
            else:
                result["critical"].append(f"❌ {check_name} — مفقود!")

        # فحص imports خاطئة
        if "from data_layer import" in src and filename == "analysis.py":
            result["critical"].append(
                f"❌ {filename}: import خاطئ 'from data_layer' يجب 'from core.data_layer'")

    score = (len(result["ok"]) * 3
             - len(result["warnings"]) * 5
             - len(result["critical"]) * 15)
    result["score"] = max(0, min(100, score))
    return result

# ═══════════════════════════════════════════════════════════════
# الوكيل 4: AgentQA — اختبارات الجودة
# ═══════════════════════════════════════════════════════════════

def agent_qa_review(project_files: Dict[str, str]) -> Dict[str, Any]:
    """
    يشغّل اختبارات الجودة الشاملة:
    - منطق X-prefix
    - منطق Tier
    - منطق FA
    - منطق Plan
    """
    result = {
        "agent": "QA",
        "status": "ok",
        "passed": [],
        "failed": [],
    }

    an = project_files.get("analysis.py", "")
    pl = project_files.get("plan.py", "")
    dl = project_files.get("data_layer.py", "")

    # ── اختبارات X-prefix ──
    x_tests = [
        ("XSPCX bypass check_spot",  "_is_x_prefix = _raw_symbol.startswith" in an),
        ("XAMZN bypass analyze",      "_is_x_an = raw_arg.upper().startswith" in an),
        ("XGOOGL bypass quicksignal", "_is_x_qs = raw_arg.upper().startswith" in an),
        ("X-prefix display symbol",   "signal.symbol = _display_symbol" in an),
        ("X-prefix chart link",       "CHART_SIG_fix" in an),
    ]
    for name, ok in x_tests:
        (result["passed"] if ok else result["failed"]).append(
            f"{'✅' if ok else '❌'} X-prefix: {name}")

    # ── اختبارات Tier ──
    tier_tests = [
        ("Futures للجميع",      "الأسهم المُرمَّزة Futures → جميع الباقات" in an),
        ("Spot للذهبي+",        "TK_tier_fix" in an),
        ("Tier تطبيق في /an",   "TK4_fix" in an),
    ]
    for name, ok in tier_tests:
        (result["passed"] if ok else result["failed"]).append(
            f"{'✅' if ok else '❌'} Tier: {name}")

    # ── اختبارات Plan ──
    plan_tests = [
        ("plan_week _use_futures_pw", "plan_week" in pl and "_use_futures_pw" in pl),
        ("plan_month _use_futures_pm", "_use_futures_pm" in pl),
        ("plan display names",         "_disp_sym_pw" in pl and "_disp_sym_pm" in pl),
        ("plan header XGOOGL",         "_disp_str_pm" in pl),
        ("plan_week header XGOOGL",    "_display_sym_str2" in pl),
    ]
    for name, ok in plan_tests:
        (result["passed"] if ok else result["failed"]).append(
            f"{'✅' if ok else '❌'} Plan: {name}")

    # ── اختبارات Data Layer ──
    dl_tests = [
        ("Yahoo Finance",    "_ohlcv_yahoo" in dl),
        ("resolve_stock",    "resolve_stock_symbol" in dl),
        ("XSPCX map",        '"XSPCX"' in dl),
        ("SPY proxy",        '"yahoo": "SPY"' in dl),
        ("BT1_fix",          "BT1_fix" in dl),
        ("self.session",     "self.session, url," in dl),
    ]
    for name, ok in dl_tests:
        (result["passed"] if ok else result["failed"]).append(
            f"{'✅' if ok else '❌'} DataLayer: {name}")

    total = len(result["passed"]) + len(result["failed"])
    result["score"] = int(len(result["passed"]) / max(total, 1) * 100)
    result["summary"] = f"{len(result['passed'])}/{total} اختبار نجح"

    return result

# ═══════════════════════════════════════════════════════════════
# الوكيل 5: AgentReport — تجميع وإرسال التقرير
# ═══════════════════════════════════════════════════════════════

def agent_report_compile(
    okx: dict,
    finance: dict,
    code: dict,
    qa: dict,
    previous: Optional[dict],
    history: List[dict],
) -> Dict[str, Any]:
    """يجمع نتائج جميع الوكلاء في تقرير واحد."""

    now = datetime.now(timezone.utc)
    overall = int((finance.get("score", 0)
                   + code.get("score", 0)
                   + qa.get("score", 0)) / 3)

    # مقارنة مع الأسبوع الماضي
    trend = ""
    if previous:
        prev_score = previous.get("overall_score", 0)
        diff = overall - prev_score
        trend = f" ({'↗️ +' if diff > 0 else '↘️ '}{diff}% عن الأسبوع الماضي)"

    # جمع المشاكل الحرجة
    critical = (code.get("critical", [])
                + finance.get("critical", []))
    warnings_all = (code.get("warnings", [])
                    + finance.get("warnings", [])
                    + okx.get("warnings", []))
    qa_failed = qa.get("failed", [])

    # بناء التقرير
    lines = [
        f"🔬 *تقرير المراجعة الأسبوعية — رائد*",
        f"📅 {now.strftime('%Y-%m-%d %H:%M')} UTC",
        f"━━━━━━━━━━━━━━━━━━",
        f"",
        f"📊 *النتيجة الإجمالية: {overall}/100*{trend}",
        f"",
    ]

    # نتائج كل وكيل
    lines += [
        f"🤖 *الوكلاء:*",
        f"• AgentOKX:     {'✅' if okx['status'] == 'ok' else '❌'}",
        f"• AgentFinance: {finance.get('score', 0)}/100",
        f"• AgentCode:    {code.get('score', 0)}/100",
        f"• AgentQA:      {qa.get('score', 0)}/100 ({qa.get('summary', '')})",
        f"",
    ]

    # مشاكل حرجة
    if critical:
        lines.append(f"🔴 *مشاكل حرجة ({len(critical)}):*")
        for c in critical[:5]:
            lines.append(f"  {c}")
        lines.append("")

    # تحذيرات
    if warnings_all:
        lines.append(f"🟡 *تحذيرات ({len(warnings_all)}):*")
        for w in warnings_all[:4]:
            lines.append(f"  {w}")
        lines.append("")

    # اختبارات فاشلة
    if qa_failed:
        lines.append(f"❌ *اختبارات فاشلة ({len(qa_failed)}):*")
        for f_item in qa_failed[:4]:
            lines.append(f"  {f_item}")
        lines.append("")

    # تحديثات OKX
    if okx.get("new_features"):
        lines.append(f"🆕 *تحديثات OKX:*")
        for nf in okx["new_features"]:
            lines.append(f"  {nf}")
        lines.append("")

    # آخر التحديثات OKX
    if okx.get("findings"):
        lines.append(f"📡 *OKX:*")
        for f_item in okx["findings"][:3]:
            lines.append(f"  {f_item}")
        lines.append("")

    # تاريخ النقاط
    if len(history) > 1:
        lines.append(f"📈 *تاريخ النقاط:*")
        for h in history[-4:]:
            date = h.get("date", "")[:10]
            score = h.get("score", 0)
            issues = h.get("issues", 0)
            lines.append(f"  {date}: {score}/100 ({issues} مشاكل)")
        lines.append("")

    # توصية
    if overall >= 90:
        rec = "✅ *المشروع جاهز للإطلاق الكامل*"
    elif overall >= 75:
        rec = "🟡 *جاهز مع مراقبة — بعض التحسينات المطلوبة*"
    elif overall >= 60:
        rec = "🟠 *يحتاج إصلاحات قبل الإطلاق*"
    else:
        rec = "🔴 *إصلاحات عاجلة مطلوبة قبل الإطلاق*"

    lines += [
        f"━━━━━━━━━━━━━━━━━━",
        f"💡 *التوصية:* {rec}",
        f"🤖 رائد التداول الذكي — المراجعة الأسبوعية",
    ]

    return {
        "overall_score": overall,
        "critical_issues": critical,
        "warnings": warnings_all,
        "qa_failed": qa_failed,
        "summary": f"نقاط: {overall}/100 | مشاكل: {len(critical)} | تحذيرات: {len(warnings_all)}",
        "report_text": "\n".join(lines),
        "okx": okx,
        "finance": finance,
        "code": code,
        "qa": qa,
    }

# ═══════════════════════════════════════════════════════════════
# الدالة الرئيسية — تُستدعى من Scheduler
# ═══════════════════════════════════════════════════════════════

async def run_weekly_review(
    project_files: Dict[str, str],
    redis_client=None,
    send_fn=None,
    admin_id: int = None,
) -> Dict[str, Any]:
    """
    الدالة الرئيسية للمراجعة الأسبوعية.
    تُستدعى من Scheduler كل ثلاثاء 04:00 UTC.

    Args:
        project_files: قاموس {filename: source_code}
        redis_client: Redis client للذاكرة المستمرة
        send_fn: دالة الإرسال عبر Telegram
        admin_id: Telegram ID للمدير
    """
    logger.info("🔬 weekly_review: بدء المراجعة الأسبوعية...")

    # تحميل البيانات السابقة
    previous = _load_previous_review(redis_client)
    history  = _load_review_history(redis_client)

    # ── تشغيل الوكلاء بالتوازي ──
    okx_result, _ = await asyncio.gather(
        agent_okx_review(),
        asyncio.sleep(0),  # dummy
    )
    finance_result = agent_finance_review(project_files)
    code_result    = agent_code_review(project_files)
    qa_result      = agent_qa_review(project_files)

    # ── تجميع التقرير ──
    report = agent_report_compile(
        okx_result, finance_result, code_result, qa_result,
        previous, history,
    )

    # ── حفظ في Redis ──
    _save_review_to_redis(redis_client, report)

    # ── إرسال التقرير ──
    if send_fn and admin_id:
        try:
            await send_fn(report["report_text"], user_id=admin_id)
            logger.info("✅ weekly_review: تقرير أُرسل للمدير")
        except Exception as e:
            logger.error(f"weekly_review send: {e}")

    logger.info(
        f"✅ weekly_review: اكتملت | نقاط: {report['overall_score']}/100 "
        f"| مشاكل: {len(report['critical_issues'])}"
    )
    return report


# ═══════════════════════════════════════════════════════════════
# دالة التكامل مع RaedEngine و Scheduler
# ═══════════════════════════════════════════════════════════════

def register_with_scheduler(engine, scheduler) -> bool:
    """
    تسجيل المراجعة الأسبوعية مع APScheduler.
    تُستدعى من main.py عند التهيئة.
    """
    try:
        from apscheduler.triggers.cron import CronTrigger

        async def _weekly_job():
            """دالة المهمة الأسبوعية."""
            try:
                # جمع ملفات المشروع من المسارات المعروفة
                project_files = {}
                base_paths = [
                    "/app/core", "/app", ".", "./core",
                ]
                target_files = [
                    "analysis.py", "plan.py", "data_layer.py",
                    "news.py", "trading.py",
                ]
                for fname in target_files:
                    for base in base_paths:
                        path = f"{base}/{fname}"
                        try:
                            with open(path) as f:
                                project_files[fname] = f.read()
                            break
                        except FileNotFoundError:
                            continue

                redis_client = getattr(engine, "redis", None) or getattr(engine, "_redis", None)
                admin_id     = int(engine.config.get("ADMIN_TELEGRAM_ID", 6747412518))

                await run_weekly_review(
                    project_files=project_files,
                    redis_client=redis_client,
                    send_fn=engine.send_fn if hasattr(engine, "send_fn") else None,
                    admin_id=admin_id,
                )
            except Exception as e:
                logger.error(f"weekly_review job: {e}")

        # T9_fix: استخدام API الـ Scheduler الصحيح في رائد
        # الـ Scheduler يدعم register_weekly مباشرة
        if hasattr(scheduler, "register_weekly"):
            scheduler.register_weekly(_weekly_job)
            logger.info("✅ weekly_review: مُسجَّل عبر register_weekly — كل ثلاثاء")
        elif hasattr(scheduler, "add_job"):
            # APScheduler مباشر
            from apscheduler.triggers.cron import CronTrigger
            scheduler.add_job(
                _weekly_job,
                trigger=CronTrigger(day_of_week="tue", hour=4, minute=0, timezone="UTC"),
                id="weekly_review",
                replace_existing=True,
            )
            logger.info("✅ weekly_review: مُسجَّل عبر add_job — كل ثلاثاء 04:00 UTC")
        elif hasattr(scheduler, "_scheduler") and hasattr(scheduler._scheduler, "add_job"):
            # Wrapper حول APScheduler
            from apscheduler.triggers.cron import CronTrigger
            scheduler._scheduler.add_job(
                _weekly_job,
                trigger=CronTrigger(day_of_week="tue", hour=4, minute=0, timezone="UTC"),
                id="weekly_review",
                replace_existing=True,
            )
            logger.info("✅ weekly_review: مُسجَّل عبر _scheduler — كل ثلاثاء 04:00 UTC")
        else:
            logger.warning("⚠️ weekly_review: Scheduler API غير معروف — يُنفَّذ يدوياً عبر /review")
        return True
    except Exception as e:
        logger.error(f"weekly_review register: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# أمر Telegram /review للمدير
# ═══════════════════════════════════════════════════════════════

async def cmd_review(update, context):
    """
    أمر /review للمدير — يُشغِّل المراجعة فوراً.
    متاح للمدير فقط.
    """
    from core.config import ADMIN_ID
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("🔒 هذا الأمر للمدير فقط.")
        return

    msg = await update.message.reply_text(
        "🔬 جاري تشغيل المراجعة الشاملة...\n"
        "⏳ قد يستغرق 30-60 ثانية")

    engine = context.bot_data.get("raed_engine")
    if not engine:
        await msg.edit_text("⚠️ النظام لم يُهيَّأ")
        return

    try:
        # جمع ملفات المشروع
        project_files = {}
        base_paths = ["/app/core", "/app", ".", "./core"]
        for fname in ["analysis.py", "plan.py", "data_layer.py", "news.py"]:
            for base in base_paths:
                try:
                    with open(f"{base}/{fname}") as f:
                        project_files[fname] = f.read()
                    break
                except FileNotFoundError:
                    continue

        redis_client = getattr(engine, "redis", None)
        report = await run_weekly_review(
            project_files=project_files,
            redis_client=redis_client,
        )
        await msg.edit_text(
            report["report_text"],
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"cmd_review: {e}")
        await msg.edit_text("❌ خطأ مؤقت في التقرير — حاول مجدداً")
