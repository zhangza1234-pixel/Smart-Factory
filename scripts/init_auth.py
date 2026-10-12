"""Initialize authentication tables; optionally provision an expert using a hidden prompt."""
import argparse
from getpass import getpass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from pydantic import ValidationError
load_dotenv(ROOT / '.env')
from auth import AuthStore, NewAccount, schema
from state_store import StateStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--username')
    parser.add_argument('--tenant')
    parser.add_argument('--plant')
    parser.add_argument('--line')
    args = parser.parse_args()
    if any((args.username, args.tenant, args.plant, args.line)) and not all((args.username, args.tenant, args.plant, args.line)):
        parser.error('创建专家需要同时填写 --username --tenant --plant --line')
    engine = StateStore().engine
    schema.create_all(engine)
    print('账户表已就绪。')
    if args.username:
        password = getpass('专家初始密码（至少12个字符）: ')
        if password != getpass('再次输入密码: '):
            parser.error('两次密码不一致')
        try:
            account = NewAccount(username=args.username, password=password, plant_id=args.plant,
                                 line_id=args.line, role='专家')
        except ValidationError:
            parser.error('账号字段无效：用户名仅允许英文字母、数字、下划线、点、短横线，密码须为12至128个字符')
        result = AuthStore(engine).create_account(args.tenant, account)
        print(f"专家账号 {result['username']} 已创建，租户 {args.tenant}。")


if __name__ == '__main__':
    main()
