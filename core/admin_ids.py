"""
مصدر واحد لأرقام المشرفين (Admin) في كل النظام.
لا أرقام ثابتة في بقية الملفات: تُقرأ من متغيرات البيئة، وإن لم تُضبط يُستخدم
رقم المالك المعتمد أدناه فقط كشبكة أمان.
"""
import os
from typing import Optional, Set

# شبكة أمان فقط (المالك المعتمد). الأفضل ضبط OWNER_CHAT_ID في Railway.
DEFAULT_OWNER_ID = 6291447764

_ENV_KEYS = ("OWNER_CHAT_ID", "ADMIN_USER_ID", "ADMIN_TELEGRAM_ID", "ADMIN_USERS")


def get_admin_ids(extra: Optional[str] = None) -> Set[int]:
    ids: Set[int] = set()
    sources = [os.environ.get(k, "") for k in _ENV_KEYS]
    if extra:
        sources.append(str(extra))
    for raw in sources:
        for part in str(raw or "").replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
    if not ids:
        ids.add(DEFAULT_OWNER_ID)
    return ids


def get_primary_admin_id() -> int:
    owner = os.environ.get("OWNER_CHAT_ID", "").strip()
    if owner.isdigit():
        return int(owner)
    return sorted(get_admin_ids())[0]
