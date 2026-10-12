"""Server-owned identities and revocable, opaque login sessions."""
import hashlib
import hmac
import json
import os
import secrets
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.concurrency import run_in_threadpool

schema = MetaData()
users = Table('auth_users', schema,
    Column('id', String(36), primary_key=True),
    Column('username', String(100), nullable=False, unique=True),
    Column('password_hash', String(250), nullable=False),
    Column('tenant_id', String(100), nullable=False),
    Column('plant_id', String(100), nullable=False),
    Column('line_id', String(100), nullable=False),
    Column('role', String(20), nullable=False),
    Column('active', Integer, nullable=False),
    Column('failed_attempts', Integer, nullable=False, server_default='0'),
    Column('locked_until', Float, nullable=False, server_default='0'))
sessions = Table('auth_sessions', schema,
    Column('token_hash', String(64), primary_key=True),
    Column('user_id', String(36), nullable=False, index=True),
    Column('expires_at', Float, nullable=False))


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=1, max_length=128, repr=False)


class NewAccount(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=12, max_length=128, repr=False)
    plant_id: str = Field(min_length=1, max_length=100)
    line_id: str = Field(min_length=1, max_length=100)
    role: Literal['操作员', '工程师', '专家']


class AccountChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=128, repr=False)


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f'scrypt${salt}${digest}'


def check_password(password, stored):
    try:
        kind, salt, _ = stored.split('$')
        return kind == 'scrypt' and hmac.compare_digest(password_hash(password, salt), stored)
    except (ValueError, TypeError):
        return False


def context(row):
    return {**{key: row[key] for key in ('tenant_id', 'plant_id', 'line_id', 'role')}, 'user_id': row['id']}


def public_account(row):
    return {**context(row), 'username': row['username'], 'active': bool(row['active'])}


class AuthStore:
    def __init__(self, engine):
        self.engine = engine

    def create_account(self, tenant_id, account):
        row = {**account.model_dump(exclude={'password'}), 'id': str(uuid4()),
               'tenant_id': tenant_id, 'password_hash': password_hash(account.password),
               'active': 1, 'failed_attempts': 0, 'locked_until': 0}
        try:
            with self.engine.begin() as conn:
                valid = conn.execute(text('SELECT l.line_id FROM production_lines l JOIN plants p '
                    'ON l.plant_id=p.plant_id WHERE p.tenant_id=:tenant_id AND '
                    'p.plant_id=:plant_id AND l.line_id=:line_id'), row).first()
                if not valid:
                    raise HTTPException(422, '工厂和产线必须属于当前租户')
                conn.execute(users.insert().values(**row))
        except IntegrityError:
            raise HTTPException(409, '用户名已存在')
        return public_account(row)

    def login(self, credentials):
        error = None
        with self.engine.begin() as conn:
            query = select(users).where(users.c.username == credentials.username).with_for_update()
            row = conn.execute(query).mappings().first()
            # Always perform a password derivation, including for unknown accounts.
            valid = check_password(credentials.password, row['password_hash'] if row else
                                   'scrypt$unknown$' + '0' * 128)
            if not row or not row['active'] or row['locked_until'] > time.time() or not valid:
                if row and row['active'] and row['locked_until'] <= time.time():
                    attempts = row['failed_attempts'] + 1
                    conn.execute(users.update().where(users.c.id == row['id']).values(
                        failed_attempts=0 if attempts >= 5 else attempts,
                        locked_until=time.time() + 900 if attempts >= 5 else 0))
                error = HTTPException(401, '账号或密码错误、账号已禁用或暂时锁定')
            else:
                conn.execute(users.update().where(users.c.id == row['id']).values(failed_attempts=0, locked_until=0))
                token = secrets.token_urlsafe(32)
                expires = time.time() + max(300, int(os.getenv('AUTH_SESSION_SECONDS', '28800')))
                conn.execute(sessions.delete().where(sessions.c.expires_at <= time.time()))
                conn.execute(sessions.insert().values(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    user_id=row['id'], expires_at=expires))
                result = {'access_token': token, 'token_type': 'bearer', 'expires_at': expires,
                          'user_ctx': context(row), 'username': row['username']}
        if error:
            raise error
        return result

    def authenticate(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.engine.connect() as conn:
            row = conn.execute(select(users).join(sessions, sessions.c.user_id == users.c.id).where(
                sessions.c.token_hash == digest, sessions.c.expires_at > time.time(), users.c.active == 1
            )).mappings().first()
        if row is None:
            raise HTTPException(401, '登录已失效，请重新登录', headers={'WWW-Authenticate': 'Bearer'})
        return context(row)


def create_auth(get_engine):
    router = APIRouter(prefix='/auth', tags=['账户与身份'])

    def store():
        return AuthStore(get_engine())

    async def authorize(request: Request):
        if request.url.path in ('/health', '/auth/login'):
            return
        header = request.headers.get('authorization', '')
        scheme, _, token = header.partition(' ')
        if scheme.lower() != 'bearer' or not token or len(token) > 200:
            raise HTTPException(401, '请先登录', headers={'WWW-Authenticate': 'Bearer'})
        try:
            ctx = await run_in_threadpool(lambda: store().authenticate(token))
        except SQLAlchemyError:
            raise HTTPException(503, '账户服务不可用，请检查数据库和账户表初始化')
        request.state.user_ctx = ctx
        request.state.token = token
        if request.method == 'POST':
            supplied = None
            content_type = request.headers.get('content-type', '')
            if 'application/json' in content_type:
                try:
                    payload = await request.json()
                    supplied = payload.get('user_ctx') if isinstance(payload, dict) else None
                except ValueError:
                    raise HTTPException(422, '请求格式无效')
            elif 'multipart/form-data' in content_type:
                form = await request.form()
                if 'user_ctx' in form:
                    try:
                        supplied = json.loads(form['user_ctx'])
                    except (ValueError, TypeError):
                        raise HTTPException(422, '用户上下文格式无效')
            if supplied is not None and supplied != ctx:
                raise HTTPException(403, '请求身份或访问范围与登录账号不一致')
        if request.url.path in ('/db-status', '/vector-status', '/memory-status', '/test_file-upload') and ctx['role'] != '专家':
            raise HTTPException(403, '此操作仅限专家')

    def expert(request):
        ctx = request.state.user_ctx
        if ctx['role'] != '专家':
            raise HTTPException(403, '只有专家可以管理账户')
        return ctx

    @router.post('/login')
    def login(credentials: Login):
        try:
            return store().login(credentials)
        except SQLAlchemyError:
            raise HTTPException(503, '账户服务不可用，请先初始化账户表')

    @router.get('/me')
    def me(request: Request):
        return {'user_ctx': request.state.user_ctx}

    @router.post('/logout')
    def logout(request: Request):
        with get_engine().begin() as conn:
            conn.execute(sessions.delete().where(sessions.c.token_hash ==
                hashlib.sha256(request.state.token.encode()).hexdigest()))
        return {'status': 'logged_out'}

    @router.get('/scopes')
    def scopes(request: Request):
        ctx = expert(request)
        with get_engine().connect() as conn:
            rows = conn.execute(text('SELECT p.plant_id, p.name AS plant_name, l.line_id, l.name AS line_name '
                'FROM plants p JOIN production_lines l ON l.plant_id=p.plant_id WHERE p.tenant_id=:tenant_id'), ctx).mappings().all()
        return {'scopes': [dict(row) for row in rows]}

    @router.get('/accounts')
    def accounts(request: Request):
        ctx = expert(request)
        with get_engine().connect() as conn:
            rows = conn.execute(select(users).where(users.c.tenant_id == ctx['tenant_id']).order_by(users.c.username)).mappings().all()
        return {'accounts': [public_account(row) for row in rows]}

    @router.post('/accounts')
    def create_account(request: Request, account: NewAccount):
        return store().create_account(expert(request)['tenant_id'], account)

    @router.post('/accounts/{user_id}')
    def change_account(user_id: str, change: AccountChange, request: Request):
        ctx = expert(request)
        if user_id == ctx['user_id']:
            raise HTTPException(409, '不能在账户管理中修改自己的账号')
        with get_engine().begin() as conn:
            row = conn.execute(select(users).where(users.c.id == user_id,
                users.c.tenant_id == ctx['tenant_id']).with_for_update()).mappings().first()
            if not row:
                raise HTTPException(404, '账号不存在')
            values = {}
            if change.active is not None:
                values['active'] = int(change.active)
            if change.password is not None:
                values.update(password_hash=password_hash(change.password), failed_attempts=0, locked_until=0)
            if not values:
                raise HTTPException(422, '请选择禁用、启用或重置密码')
            conn.execute(users.update().where(users.c.id == user_id).values(**values))
            conn.execute(sessions.delete().where(sessions.c.user_id == user_id))
        return {'status': 'updated'}

    return router, authorize
