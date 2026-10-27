"""多用户 RBAC：应用层用户与角色（对应 Utopia 的 owner/admin/editor/viewer）。

角色权限（从低到高）：
- viewer —— 只读
- editor —— 可写事实/文档
- admin  —— 可合并实体/管理用户
- owner  —— 完全控制（首个注册用户）

新密码使用 Werkzeug 的密码哈希；旧版 SHA-256 哈希在成功登录后升级。
本模块只提供用户/角色/鉴权的数据与判断；把 has_role 接到具体端点即可实现权限控制。
"""

from __future__ import annotations

import hashlib
import hmac
import sqlite3

from werkzeug.security import check_password_hash, generate_password_hash

ROLES = ("viewer", "editor", "admin", "owner")
_LEVEL = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}


def create_user(conn: sqlite3.Connection, username: str, password: str, role: str = "viewer") -> None:
    if role not in ROLES:
        raise ValueError(f"role 必须是 {ROLES} 之一，收到 {role!r}")
    conn.execute(
        "INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?)",
        (username, _hash(password), role),
    )
    conn.commit()


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> dict | None:
    r = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if r and _verify(password, r["password_hash"]):
        if not r["password_hash"].startswith(("scrypt:", "pbkdf2:")):
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (_hash(password), r["id"]))
            conn.commit()
        return dict(r)
    return None


def ensure_default_admin(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM users WHERE username=?", ("admin",)).fetchone():
        return
    conn.execute(
        "INSERT OR IGNORE INTO users(username, password_hash, role) VALUES (?, ?, ?)",
        ("admin", _hash("admin123"), "admin"),
    )
    conn.commit()


def set_password(conn: sqlite3.Connection, username: str, password: str) -> None:
    if not password:
        raise ValueError("密码不能为空")
    cursor = conn.execute("UPDATE users SET password_hash=? WHERE username=?", (_hash(password), username))
    if cursor.rowcount != 1:
        raise ValueError("用户不存在")
    conn.commit()


def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, username, role FROM users ORDER BY id").fetchall()


def has_role(user: dict | None, *roles: str) -> bool:
    """判断用户是否具备 roles 中任一角色（按权限等级取最高）。"""
    if not user or user.get("role") not in _LEVEL:
        return False
    needed = min((_LEVEL.get(r, -1) for r in roles), default=-1)
    return _LEVEL[user["role"]] >= needed


def _hash(password: str) -> str:
    return generate_password_hash(password)


def _verify(password: str, stored: str) -> bool:
    if stored.startswith(("scrypt:", "pbkdf2:")):
        return check_password_hash(stored, password)
    if stored.count("$") != 1:
        return False
    salt, digest = stored.split("$", 1)
    return hmac.compare_digest(hashlib.sha256((salt + password).encode()).hexdigest(), digest)
