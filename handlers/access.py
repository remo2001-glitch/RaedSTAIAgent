"""
handlers/access.py — بوابة الحماية قبل كل المعالجات (الدفعة 69) + أمر /access للمدراء.
تُسجَّل في المجموعة -100 فتمرّ عليها كل التحديثات قبل أي أمر؛ ApplicationHandlerStop يوقف ما بعدها.
"""
import logging

from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ApplicationHandlerStop, ContextTypes, TypeHandler, CommandHandler

from core import access_guard as G

logger = logging.getLogger("RAED.access")

_left_chats = set()


async def _notify_owner(context, text: str) -> None:
    o = G.owner_id()
    if not o:
        return
    try:
        await context.bot.send_message(chat_id=o, text=text, disable_web_page_preview=True)
    except Exception as e:
        logger.info(f"access notify owner: {type(e).__name__}")


async def _leave(context, chat, who=None, reason="") -> None:
    cid = getattr(chat, "id", None)
    if cid is None or cid in _left_chats:
        return
    _left_chats.add(cid)
    try:
        await context.bot.leave_chat(cid)
    except Exception as e:
        logger.info(f"access leave {cid}: {type(e).__name__}")
    _title = getattr(chat, "title", "") or getattr(chat, "username", "") or str(cid)
    _who = ""
    if who is not None:
        _who = f"\nأضافه: {getattr(who, 'full_name', '') or ''} ({getattr(who, 'id', '')})"
    await _notify_owner(context, f"🛡️ غادر رائد {('قناة' if getattr(chat, 'type', '') == ChatType.CHANNEL else 'مجموعة')}: "
                                 f"{_title} ({cid}){_who}\n{reason or 'البوت يعمل في المحادثات الخاصة فقط'}")


async def gate(update: object, context: ContextTypes.DEFAULT_TYPE):
    if not isinstance(update, Update):
        raise ApplicationHandlerStop
    chat = update.effective_chat
    # 1) خارج المحادثات الخاصة: مغادرة فورية بلا استثناء (قرار رحال)
    if update.my_chat_member is not None:
        mcm = update.my_chat_member
        if mcm.chat.type != ChatType.PRIVATE and mcm.new_chat_member.status in ("member", "administrator", "restricted"):
            await _leave(context, mcm.chat, mcm.from_user, "أُضيف البوت دون أن يكون ذلك مسموحاً")
        raise ApplicationHandlerStop
    if chat is not None and chat.type != ChatType.PRIVATE:
        await _leave(context, chat, getattr(update, "effective_user", None))
        raise ApplicationHandlerStop
    if update.inline_query is not None or update.chosen_inline_result is not None:
        raise ApplicationHandlerStop
    user = update.effective_user
    if user is None or getattr(user, "is_bot", False):
        raise ApplicationHandlerStop
    uid = int(user.id)
    # 2) الوضع الخاص
    if not G.is_allowed(uid):
        if update.callback_query is not None:
            try:
                await update.callback_query.answer(G.DENY_TEXT, show_alert=True)
            except Exception:
                pass
        elif update.effective_message is not None and G.should_reply_deny(uid):
            try:
                await update.effective_message.reply_text(G.DENY_TEXT)
            except Exception:
                pass
        raise ApplicationHandlerStop
    # 3) الإغراق
    ok, just_muted = G.flood_check(uid)
    if not ok:
        if just_muted:
            try:
                if update.effective_message is not None:
                    await update.effective_message.reply_text(
                        f"⏸️ طلبات كثيرة متتالية — أُوقف حسابك مؤقتاً {G.FLOOD_MUTE_SEC // 60} دقائق.")
            except Exception:
                pass
            await _notify_owner(context, f"🛡️ إغراق طلبات من {user.full_name} ({uid}) — حظر مؤقت {G.FLOOD_MUTE_SEC // 60} دقائق")
        if update.callback_query is not None:
            try:
                await update.callback_query.answer()
            except Exception:
                pass
        raise ApplicationHandlerStop
    msg = update.message or update.edited_message
    if msg is None:
        return
    # 4) الأوامر المخفية + الحد اليومي
    cmd, args = G.parse_command(msg.text or msg.caption or "")
    if cmd:
        if not G.command_visible(uid, cmd, args):
            logger.warning(f"access: أمر مخفي /{cmd} من {uid}")
            if G.record_probe(uid):
                await _notify_owner(context, f"🛡️ محاولات متكررة لأوامر المدير من {user.full_name} ({uid}) — آخرها /{cmd}")
            raise ApplicationHandlerStop     # يُتجاهل كأنه غير موجود
        if cmd in G.ANALYSIS_COMMANDS and not G.quota_take(uid):
            await msg.reply_text(f"⏳ وصلت إلى الحد اليومي ({G.DAILY_ANALYSIS_LIMIT} تحليلات). يتجدد الساعة 00:00 بتوقيت السعودية.")
            raise ApplicationHandlerStop
    elif msg.photo or (msg.document and str(getattr(msg.document, "mime_type", "")).startswith("image/")):
        if not G.quota_take(uid):    # تحليل الشارت بالصورة
            await msg.reply_text(f"⏳ وصلت إلى الحد اليومي ({G.DAILY_ANALYSIS_LIMIT} تحليلات). يتجدد الساعة 00:00 بتوقيت السعودية.")
            raise ApplicationHandlerStop


async def cmd_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/access add|remove ID — /access list (للمدراء؛ البوابة تخفيه عن غيرهم)."""
    uid = update.effective_user.id
    if not G.is_admin(uid):
        return
    args = context.args or []
    sub = (args[0].lower() if args else "list")
    try:
        if sub in ("add", "remove") and len(args) >= 2:
            t = int(str(args[1]).strip())
            if sub == "add":
                G.allow_add(t)
            else:
                G.allow_remove(t)
                try:
                    from core.state_manager import state_manager as _s
                    if _s.is_autotrade_on(t) and not G.is_admin(t):
                        _s.set_autotrade_on(t, False)
                except Exception:
                    pass
            head = f"✅ أُضيف `{t}` إلى الحسابات المسموح لها" if sub == "add" else f"⏹️ أُزيل `{t}` — لم يعد يستطيع استخدام البوت"
        elif sub == "list":
            head = ""
        else:
            await update.message.reply_text(
                "الاستخدام:\n/access list\n/access add رقم\\_الحساب\n/access remove رقم\\_الحساب",
                parse_mode=ParseMode.MARKDOWN)
            return
    except ValueError:
        await update.message.reply_text("⚠️ أدخل رقم حساب صحيحاً")
        return
    al = sorted(G.allow_list())
    adm = sorted(G.all_admins())
    L = ([head, ""] if head else []) + [
        "🛡️ *الوضع الخاص — من يستطيع استخدام رائد*",
        "━━━━━━━━━━━━━━━━━━",
        "👑 المالك والمدراء: " + "، ".join(f"`{a}`" + (" (المالك)" if G.is_owner(a) else "") for a in adm),
        f"✅ حسابات مضافة ({len(al)}): " + ("، ".join(f"`{a}`" for a in al) if al else "لا يوجد"),
        "",
        f"الحد اليومي لغير المدراء: {G.DAILY_ANALYSIS_LIMIT} تحليلات",
    ]
    await update.message.reply_text("\n".join(L), parse_mode=ParseMode.MARKDOWN)


def register(app) -> None:
    app.add_handler(TypeHandler(Update, gate), group=-100)
    app.add_handler(CommandHandler("access", cmd_access))
