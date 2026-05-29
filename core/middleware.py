"""
🔐 رائد — Middleware
للتحقق من الباقة في كل أمر
"""

import logging
_mw_logger = logging.getLogger("core.middleware")

def require_tier(command: str):
    """
    Decorator يتحقق من باقة المستخدم قبل تنفيذ أي أمر.
    يُسجّل محاولات الوصول غير المصرح بها.
    """
    def decorator(func):
        import functools
        @functools.wraps(func)
        async def wrapper(update, context):
            from core.state_manager import state_manager as _sm
            user_id = update.effective_user.id if update.effective_user else 0

            if not _sm.can_use_command(user_id, command):
                user_tier = _sm.get_tier(user_id)
                _mw_logger.warning(
                    f"BLOCKED: user {user_id} ({user_tier})"
                    f" → /{command} (يحتاج أعلى)")
                reason = _sm.get_blocked_reason(user_id, command)
                try:
                    await update.message.reply_text(
                        reason, parse_mode="Markdown")
                except Exception:
                    pass
                return
            return await func(update, context)
        return wrapper
    return decorator


def admin_only(func):
    """
    Decorator يسمح فقط للمدير.
    يُسجّل أي محاولة غير مصرح بها.
    """
    import functools
    @functools.wraps(func)
    async def wrapper(update, context):
        from core.state_manager import state_manager as _sm
        user_id = update.effective_user.id if update.effective_user else 0
        if _sm.get_tier(user_id) != "admin":
            _mw_logger.warning(
                f"ADMIN BLOCKED: user {user_id} → {func.__name__}")
            try:
                await update.message.reply_text(
                    "⛔ هذا الأمر للمدير فقط")
            except Exception:
                pass
            return
        return await func(update, context)
    return wrapper
