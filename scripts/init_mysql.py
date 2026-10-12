"""Initialize an installed MySQL server; admin password is entered interactively."""
import argparse
from getpass import getpass
import os
from pathlib import Path
import re
import pymysql
from pymysql.constants import CLIENT
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]


def run_sql(connection, path):
    with connection.cursor() as cursor:
        cursor.execute(path.read_text(encoding="utf-8"))
        while cursor.nextset():
            pass


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-user", default="root")
    parser.add_argument("--seed", action="store_true", help="导入开发演示数据，保留已有同 ID 记录")
    args = parser.parse_args()
    database = os.getenv("MYSQL_DATABASE", "factory_kb")
    if not re.fullmatch(r"[a-zA-Z0-9_]{1,64}", database):
        parser.error("MYSQL_DATABASE 只能包含字母、数字、下划线，最多 64 字符")
    user = os.getenv("MYSQL_USER", "factory_app")
    password = os.getenv("MYSQL_PASSWORD", "")
    if user != args.admin_user and not password:
        parser.error("请先在 .env 填写 MYSQL_PASSWORD（应用账户密码）")
    connection = pymysql.connect(host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")), user=args.admin_user,
        password=getpass("MySQL 管理员密码: "), charset="utf8mb4", autocommit=True,
        client_flag=CLIENT.MULTI_STATEMENTS, connect_timeout=5, read_timeout=30, write_timeout=30)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
            connection.select_db(database)
        run_sql(connection, ROOT / "database" / "01_schema.sql")
        run_sql(connection, ROOT / "database" / "03_rag_state.sql")
        run_sql(connection, ROOT / "database" / "04_auth.sql")
        if args.seed:
            run_sql(connection, ROOT / "database" / "02_demo.sql")
        if user != args.admin_user:
            with connection.cursor() as cursor:
                cursor.execute("CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s", (user, "%", password))
                cursor.execute(f"GRANT SELECT ON `{database}`.* TO %s@%s", (user, "%"))
                for table in ('device_parameters', 'fault_knowledge'):
                    cursor.execute(f"GRANT INSERT, UPDATE ON `{database}`.`{table}` TO %s@%s", (user, "%"))
                for table in ('rag_document_families', 'rag_document_versions', 'rag_sessions', 'rag_messages', 'rag_user_memories', 'auth_users', 'auth_sessions'):
                    cursor.execute(f"GRANT INSERT, UPDATE, DELETE ON `{database}`.`{table}` TO %s@%s", (user, "%"))
        print(f"数据库 {database} 已就绪；应用账户 {user}。已有账户的密码和权限不会被重置。")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
