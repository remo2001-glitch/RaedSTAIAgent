"""
🔐 رائد — Middleware
للتحقق من الباقة في كل أمر
"""

def require_tier(command: str):
    """
    Decorator يتحقق من باقة المستخدم قبل تنفيذ أي أمر.
    الاستخدام: @require_tier("signal")
    """
    def decorator(func):
        import functools
        @functools.wraps(func)
        async def wrapper(update, context):
            from core.state_manager import state_manager as _sm
            engine  = context.bot_data.get("raed_engine")
            user_id = update.effective_user.id if update.effective_user else 0
            
            if not _sm.can_use_command(user_id, command):
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
