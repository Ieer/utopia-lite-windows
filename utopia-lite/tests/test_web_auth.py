import hashlib
import importlib
import os
import sqlite3
import tempfile
import unittest


class WebAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.old_db = os.environ.get("UTOPIA_LITE_DB")
        os.environ["UTOPIA_LITE_DB"] = os.path.join(cls.tempdir.name, "test.db")
        cls.dash_app = importlib.import_module("dash_app")
        cls.db = importlib.import_module("app.db")
        cls.rbac = importlib.import_module("app.rbac")
        cls.server = cls.dash_app.app.server
        cls.server.config["TESTING"] = True

    @classmethod
    def tearDownClass(cls):
        if cls.old_db is None:
            os.environ.pop("UTOPIA_LITE_DB", None)
        else:
            os.environ["UTOPIA_LITE_DB"] = cls.old_db
        cls.tempdir.cleanup()

    def setUp(self):
        self.client = self.server.test_client()

    def login(self, password="admin123"):
        self.client.get("/login")
        with self.client.session_transaction() as state:
            csrf = state["csrf_token"]
        return self.client.post("/login", data={"username": "admin", "password": password, "csrf_token": csrf})

    def test_login_protects_dash_and_logout(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/_dash-layout").status_code, 401)
        self.assertEqual(self.client.post("/_dash-update-component", json={}).status_code, 401)
        self.assertEqual(self.client.get("/assets/style.css").status_code, 302)
        self.assertIn("用户名或密码错误", self.login("wrong").get_data(as_text=True))
        self.assertEqual(self.client.get("/_dash-layout").status_code, 401)
        self.assertEqual(self.login().status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/_dash-layout").status_code, 200)
        self.assertEqual(self.client.get("/_dash-dependencies").status_code, 200)
        self.assertEqual(self.client.post("/logout", data={"csrf_token": "错误"}).status_code, 403)
        with self.client.session_transaction() as state:
            csrf = state["csrf_token"]
        self.assertEqual(self.client.post("/logout", data={"csrf_token": csrf}).status_code, 302)
        self.assertEqual(self.client.get("/_dash-layout").status_code, 401)

    def test_route_callback_renders_logout_form(self):
        self.login()
        response = self.client.post("/_dash-update-component", json={
            "output": "route-content.children",
            "outputs": {"id": "route-content", "property": "children"},
            "inputs": [{"id": "route-location", "property": "pathname", "value": "/"}],
            "state": [],
            "changedPropIds": ["route-location.pathname"],
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("退出登录", response.get_data(as_text=True))
        def find_logout(value):
            if isinstance(value, dict):
                return value.get("action") == "/logout" or any(find_logout(item) for item in value.values())
            if isinstance(value, list):
                return any(find_logout(item) for item in value)
            return False
        self.assertTrue(find_logout(response.get_json()))

    def test_csrf_required_for_login(self):
        self.client.get("/login")
        self.assertEqual(self.client.post("/login", data={"username": "admin", "password": "admin123"}).status_code, 403)

    def test_existing_admin_password_is_not_reset(self):
        conn = self.db.init_db()
        try:
            self.rbac.set_password(conn, "admin", "new-password")
            self.rbac.ensure_default_admin(conn)
            self.assertIsNone(self.rbac.authenticate(conn, "admin", "admin123"))
            self.assertIsNotNone(self.rbac.authenticate(conn, "admin", "new-password"))
        finally:
            self.rbac.set_password(conn, "admin", "admin123")
            conn.close()

    def test_legacy_password_is_upgraded(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, password_hash TEXT, role TEXT)")
        salt = "abc"
        digest = hashlib.sha256((salt + "old-password").encode()).hexdigest()
        conn.execute("INSERT INTO users VALUES (1, 'old', ?, 'viewer')", (salt + "$" + digest,))
        self.assertIsNotNone(self.rbac.authenticate(conn, "old", "old-password"))
        stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
        self.assertTrue(stored.startswith(("scrypt:", "pbkdf2:")))
        self.assertIsNotNone(self.rbac.authenticate(conn, "old", "old-password"))
        conn.close()


if __name__ == "__main__":
    unittest.main()
