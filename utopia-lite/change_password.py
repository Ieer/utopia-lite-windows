"""本机交互式修改已有用户密码。"""

import argparse
import getpass

from app import db, rbac


def main():
    parser = argparse.ArgumentParser(description="修改 Utopia Lite 用户密码")
    parser.add_argument("username", help="已有用户名")
    args = parser.parse_args()
    password = getpass.getpass("新密码: ")
    if password != getpass.getpass("再次输入新密码: "):
        parser.error("两次输入的密码不一致")
    conn = db.init_db()
    try:
        rbac.set_password(conn, args.username, password)
    finally:
        conn.close()
    print("密码已更新；请重启 Dash 服务以使现有会话失效。")


if __name__ == "__main__":
    main()
