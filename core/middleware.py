"""
🔐 رائد — Middleware v2
التحقق من الباقة في كل أمر مع رسائل احترافية
"""
import logging
import functools
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

_mw_logger = logging.getLogger("core.middleware")


def require_tier(command: str):
    """
    Decorator يتحقق من باقة المستخدم قبل تنفيذ الأمر.
    يُعيد رسالة ترقية واضحة إذا كانت الباقة غير كافية.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          *args, **kwargs):
            if not update.effective_user:
                return
            user_id = update.effective_user.id
            try:
                from core.state_manager import state_manager as _sm
                if not _sm.can_use_command(user_id, command):
                    msg = _sm.get_blocked_reason(user_id, command)
                    if update.message:
                        await update.message.reply_text(
                            msg, parse_mode=ParseMode.MARKDOWN
                        )
                    elif update.callback_query:
                        await update.callback_query.answer(
                            "🔒 باقتك لا تدعم هذا الأمر", show_alert=True
                        )
                    return
            except Exception as e:
                _mw_logger.error(f"middleware ({command}): {e}")
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator


def require_live_trading(func):
    """
    Decorator يمنع المجاني من ربط منصة تداول حقيقية.
    """
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      *args, **kwargs):
        if not update.effective_user:
            return
        user_id = update.effective_user.id
        try:
            from core.state_manager import state_manager as _sm
            if not _sm.can_use_live_trading(user_id):
                await update.message.reply_text(
                    "🔒 ربط منصة التداول الحقيقي غير متاح في الباقة المجانية\n\n"
                    "للترقية: /upgrade",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
        except Exception as e:
            _mw_logger.error(f"require_live_trading: {e}")
        return await func(update, context, *args, **kwargs)
    return wrapper
