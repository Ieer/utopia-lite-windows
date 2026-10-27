"""Dash 登录会话；独立的 FastAPI 服务不受此会话保护。"""

from __future__ import annotations

import hmac
import secrets
from datetime import timedelta

from flask import abort, redirect, render_template, request, session, url_for

from . import db, rbac


def init_auth(server):
    server.secret_key = secrets.token_bytes(32)
    server.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    conn = db.init_db()
    try:
        rbac.ensure_default_admin(conn)
    finally:
        conn.close()

    @server.before_request
    def require_login():
        if request.endpoint == "login" or session.get("user_id"):
            return None
        if request.path.startswith("/_dash-"):
            return "请重新登录。", 401
        return redirect(url_for("login"))

    @server.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET" and session.get("user_id"):
            return redirect("/")
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        error = None
        if request.method == "POST":
            validate_csrf()
            conn = db.init_db()
            try:
                user = rbac.authenticate(conn, request.form.get("username", ""), request.form.get("password", ""))
            finally:
                conn.close()
            if user:
                session.clear()
                session.permanent = True
                session["user_id"] = user["id"]
                session["csrf_token"] = secrets.token_urlsafe(32)
                return redirect("/")
            error = "用户名或密码错误，请重试。"
        response = server.make_response(render_template("login.html", error=error, csrf_token=session["csrf_token"]))
        response.headers["Cache-Control"] = "no-store"
        return response

    @server.post("/logout")
    def logout():
        validate_csrf()
        session.clear()
        return redirect(url_for("login"))


def validate_csrf():
    expected = session.get("csrf_token", "")
    supplied = request.form.get("csrf_token", "")
    if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
        abort(403)
