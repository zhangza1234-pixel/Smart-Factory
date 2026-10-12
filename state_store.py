"""Authoritative version catalog and user-scoped conversation persistence.

MySQL in the app, SQLite in tests. Redis is a disposable cache, never authority.
"""
from contextlib import contextmanager
from datetime import date
from hashlib import sha256
import json
import os
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import (create_engine, inspect, MetaData, Table, Column, String, Text, Float,
                        Integer, UniqueConstraint, select, and_, or_, text)
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError


schema = MetaData()
families = Table('rag_document_families', schema,
    Column('id', String(64), primary_key=True), Column('name', String(200), nullable=False))
versions = Table('rag_document_versions', schema,
    Column('id', String(64), primary_key=True), Column('family_id', String(64), nullable=False),
    Column('family_name', String(200), nullable=False), Column('version', String(80), nullable=False),
    Column('tenant_id', String(100), nullable=False), Column('plant_id', String(100), nullable=False),
    Column('line_id', String(100), nullable=False), Column('access_level', String(20), nullable=False),
    Column('title', String(200), nullable=False), Column('filename', String(300), nullable=False),
    Column('category', String(100), nullable=False, server_default=''),
    Column('effective_from', String(10), nullable=False), Column('effective_to', String(10), nullable=False),
    Column('status', String(20), nullable=False), Column('supersedes', String(64), nullable=False),
    Column('chunk_count', Integer, nullable=False), Column('created_at', Float, nullable=False),
    UniqueConstraint('family_id', 'version', name='uq_rag_family_version'))
sessions = Table('rag_sessions', schema,
    Column('id', String(36), primary_key=True), Column('owner', String(64), nullable=False),
    Column('summary', Text, nullable=False), Column('summary_through', Integer, nullable=False),
    Column('revision', Integer, nullable=False), Column('lease', String(36), nullable=False),
    Column('busy_until', Float, nullable=False), Column('updated_at', Float, nullable=False))
messages = Table('rag_messages', schema,
    Column('session_id', String(36), primary_key=True), Column('ordinal', Integer, primary_key=True),
    Column('role', String(20), nullable=False), Column('content', Text, nullable=False),
    Column('sources', Text, nullable=False))
memories = Table('rag_user_memories', schema,
    Column('id', String(36), primary_key=True), Column('owner', String(64), nullable=False),
    Column('kind', String(30), nullable=False), Column('content', Text, nullable=False),
    Column('source_id', String(64), nullable=False), Column('valid_until', String(10), nullable=False),
    Column('created_at', Float, nullable=False))


def family_identity(ctx, name):
    return sha256(json.dumps([ctx.tenant_id, ctx.plant_id, ctx.line_id, name.strip()],
                             ensure_ascii=False).encode()).hexdigest()


def owner_identity(ctx):
    # Include role so a role downgrade cannot load prior privileged conversation.
    return sha256(json.dumps([ctx.tenant_id, ctx.plant_id, ctx.line_id,
                             getattr(ctx, 'user_id', 'demo'), ctx.role]).encode()).hexdigest()


class StateStore:
    def __init__(self, engine=None, redis_url=None):
        if engine is None:
            engine = create_engine(URL.create('mysql+pymysql',
                username=os.getenv('MYSQL_USER', 'factory_app'), password=os.getenv('MYSQL_PASSWORD', ''),
                host=os.getenv('MYSQL_HOST', '127.0.0.1'), port=int(os.getenv('MYSQL_PORT', '3306')),
                database=os.getenv('MYSQL_DATABASE', 'factory_kb'), query={'charset': 'utf8mb4'}),
                pool_pre_ping=True, connect_args={'connect_timeout': 5})
        self.engine = engine
        schema.create_all(engine)
        if 'category' not in {column['name'] for column in inspect(engine).get_columns('rag_document_versions')}:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE rag_document_versions ADD COLUMN category VARCHAR(100) NOT NULL DEFAULT ''"))
        self.redis = None
        self.cache_status = 'mysql_only'
        url = redis_url if redis_url is not None else os.getenv('REDIS_URL', '')
        if url:
            try:
                from redis import Redis
                self.redis = Redis.from_url(url, decode_responses=True,
                    socket_connect_timeout=1, socket_timeout=1)
                self.redis.ping()
                self.cache_status = 'connected'
            except Exception:
                self.cache_status = 'unavailable_mysql_fallback'
        self.ttl = max(60, int(os.getenv('MEMORY_TTL_SECONDS', '86400')))

    def refresh_cache_status(self):
        if self.redis:
            try:
                self.redis.ping()
                self.cache_status = 'connected'
            except Exception:
                self.cache_status = 'unavailable_mysql_fallback'
        return self.cache_status

    def stage(self, document_id, ctx, metadata, chunk_count):
        """Reserve a version before embedding; partial vector writes stay invisible."""
        fid = metadata['family_id']
        try:
            with self.engine.begin() as conn:
                family = conn.execute(select(families).where(families.c.id == fid).with_for_update()).first()
                if not family:
                    conn.execute(families.insert().values(id=fid, name=metadata['family_name']))
                existing = conn.execute(select(versions).where(
                    versions.c.family_id == fid, versions.c.version == metadata['version'])).mappings().first()
                if existing:
                    if existing['id'] != document_id:
                        raise HTTPException(409, '同一文档系列的版本号已存在，请使用新版本号')
                    return dict(existing)
                row = {key: metadata[key] for key in ('family_name', 'version', 'tenant_id', 'plant_id',
                    'line_id', 'access_level', 'title', 'effective_from', 'effective_to')}
                row.update(id=document_id, family_id=fid, filename=metadata['original_filename'],
                           category=metadata.get('category', '未分类'),
                           status='draft', supersedes='', chunk_count=chunk_count, created_at=time.time())
                conn.execute(versions.insert().values(**row))
                return row
        except IntegrityError:
            raise HTTPException(409, '该文档系列正在更新，请稍后重试')

    def catalog(self, ctx):
        with self.engine.connect() as conn:
            rows = conn.execute(select(versions).where(versions.c.tenant_id == ctx.tenant_id,
                versions.c.plant_id == ctx.plant_id, versions.c.line_id.in_([ctx.line_id, '']))).mappings()
            return [dict(row) for row in rows]

    def change_status(self, document_id, ctx, status):
        if ctx.role != '专家':
            raise HTTPException(403, '只有专家可以发布或撤回版本')
        with self.engine.begin() as conn:
            row = conn.execute(select(versions).where(versions.c.id == document_id)).mappings().first()
            if not row or (row['tenant_id'], row['plant_id'], row['line_id']) != (ctx.tenant_id, ctx.plant_id, ctx.line_id):
                raise HTTPException(404, '文档不存在或不属于当前范围')
            conn.execute(select(families).where(families.c.id == row['family_id']).with_for_update()).first()
            row = conn.execute(select(versions).where(versions.c.id == document_id).with_for_update()).mappings().one()
            if row['status'] == status:
                return dict(row)
            if status == 'withdrawn' and row['status'] == 'draft':
                raise HTTPException(422, '草稿尚未发布，无需撤回')
            previous = ''
            if status == 'published':
                published = conn.execute(select(versions).where(versions.c.family_id == row['family_id'],
                    versions.c.status == 'published', versions.c.id != document_id).with_for_update()).mappings().all()
                if any(item['effective_from'] == row['effective_from'] for item in published):
                    raise HTTPException(409, '同系列已有相同生效日期的发布版本，请先撤回错误版本')
                older = [item for item in published if item['effective_from'] < row['effective_from']]
                if older:
                    previous = max(older, key=lambda item: item['effective_from'])['id']
            conn.execute(versions.update().where(versions.c.id == document_id).values(status=status, supersedes=previous))
            return {**dict(row), 'status': status, 'supersedes': previous}

    def selected_versions(self, ctx, as_of):
        """Pick latest released start per family BEFORE ACL and expiry checks.

        An expired/restricted successor must not silently revive an obsolete version.
        A withdrawn successor is a tombstone from its start date (fail closed).
        """
        from rag import authorized
        groups = {}
        for row in self.catalog(ctx):
            if row['status'] == 'draft' or row['effective_from'] > as_of.isoformat():
                continue
            if row['family_id'] not in groups or (row['effective_from'], row['status'] == 'published') > (
                    groups[row['family_id']]['effective_from'], groups[row['family_id']]['status'] == 'published'):
                groups[row['family_id']] = row
        return {row['id']: row for row in groups.values() if row['status'] == 'published'
                and (not row['effective_to'] or as_of.isoformat() < row['effective_to'])
                and authorized(row, ctx)}

    @contextmanager
    def conversation(self, ctx, session_id=None):
        sid, owner, token = session_id or str(uuid4()), owner_identity(ctx), str(uuid4())
        now = time.time()
        try:
            with self.engine.begin() as conn:
                row = conn.execute(select(sessions).where(sessions.c.id == sid)).mappings().first()
                if not row:
                    conn.execute(sessions.insert().values(id=sid, owner=owner, summary='{}', summary_through=0,
                        revision=0, lease='', busy_until=0, updated_at=now))
                elif row['owner'] != owner:
                    raise HTTPException(404, '会话不存在')
                acquired = conn.execute(sessions.update().where(sessions.c.id == sid,
                    sessions.c.owner == owner, sessions.c.busy_until <= now).values(lease=token, busy_until=now + 900))
                if not acquired.rowcount:
                    raise HTTPException(409, '当前会话正在回答，请等待完成后重试')
        except IntegrityError:
            raise HTTPException(409, '当前会话正在创建，请稍后重试')
        try:
            yield sid, token, self.history(ctx, sid)
        finally:
            with self.engine.begin() as conn:
                conn.execute(sessions.update().where(sessions.c.id == sid, sessions.c.lease == token)
                             .values(lease='', busy_until=0))

    def history(self, ctx, sid):
        with self.engine.connect() as conn:
            row = conn.execute(select(sessions).where(sessions.c.id == sid,
                sessions.c.owner == owner_identity(ctx))).mappings().first()
            if not row:
                raise HTTPException(404, '会话不存在')
            # Cache revision prevents stale history when Redis was unavailable during a write.
            key = f"rag:session:{row['owner']}:{sid}"
            if self.redis:
                try:
                    cached = self.redis.get(key)
                    if cached:
                        value = json.loads(cached)
                        if value.get('revision') == row['revision']:
                            return value['history']
                except Exception:
                    self.cache_status = 'unavailable_mysql_fallback'
            items = conn.execute(select(messages).where(messages.c.session_id == sid,
                messages.c.ordinal > row['summary_through']).order_by(messages.c.ordinal)).mappings().all()
            result = {'summary': json.loads(row['summary']), 'summary_through': row['summary_through'],
                      'messages': [{**dict(item), 'sources': json.loads(item['sources'])} for item in items]}
            if self.redis:
                try:
                    self.redis.setex(key, self.ttl, json.dumps({'revision': row['revision'], 'history': result}, ensure_ascii=False))
                    self.cache_status = 'connected'
                except Exception:
                    self.cache_status = 'unavailable_mysql_fallback'
            return result

    def save_turn(self, ctx, sid, token, question, result, summary=None, summary_through=None):
        with self.engine.begin() as conn:
            row = conn.execute(select(sessions).where(sessions.c.id == sid,
                sessions.c.owner == owner_identity(ctx), sessions.c.lease == token).with_for_update()).mappings().first()
            if not row:
                raise HTTPException(409, '会话已更新，请重试')
            revision = row['revision'] + 1
            conn.execute(messages.insert(), [
                {'session_id': sid, 'ordinal': revision * 2 - 1, 'role': 'user', 'content': question, 'sources': '[]'},
                {'session_id': sid, 'ordinal': revision * 2, 'role': 'assistant', 'content': result['answer'],
                 'sources': json.dumps([item['metadata'].get('document_id', '') for item in result['sources']])}])
            values = {'revision': revision, 'updated_at': time.time()}
            if summary is not None:
                values.update(summary=json.dumps(summary, ensure_ascii=False), summary_through=summary_through)
            conn.execute(sessions.update().where(sessions.c.id == sid).values(**values))
        self._invalidate_session(ctx, sid)

    def _invalidate_session(self, ctx, sid):
        if self.redis:
            try:
                self.redis.delete(f'rag:session:{owner_identity(ctx)}:{sid}')
            except Exception:
                self.cache_status = 'unavailable_mysql_fallback'

    def clear_session(self, ctx, sid):
        with self.engine.begin() as conn:
            row = conn.execute(select(sessions).where(sessions.c.id == sid,
                sessions.c.owner == owner_identity(ctx)).with_for_update()).mappings().first()
            if not row:
                raise HTTPException(404, '会话不存在')
            if row['busy_until'] > time.time():
                raise HTTPException(409, '请等待当前回答完成后再清除')
            conn.execute(messages.delete().where(messages.c.session_id == sid))
            # Revision remains monotonic: old cached revisions can never become live again.
            conn.execute(sessions.update().where(sessions.c.id == sid).values(summary='{}', summary_through=0,
                revision=row['revision'] + 1, updated_at=time.time()))
        self._invalidate_session(ctx, sid)

    def list_memories(self, ctx):
        with self.engine.connect() as conn:
            rows = conn.execute(select(memories).where(memories.c.owner == owner_identity(ctx)).order_by(memories.c.created_at)).mappings()
            return [dict(row) for row in rows]

    def save_memory(self, ctx, kind, content, source_id='', valid_until=''):
        if kind == 'reviewed_experience' and ctx.role != '专家':
            raise HTTPException(403, '只有专家可以登记审核经验')
        if source_id and source_id not in self.selected_versions(ctx, date.today()):
            raise HTTPException(422, '关联来源必须是当前可访问的有效版本')
        existing = self.list_memories(ctx)
        if len(existing) >= 50:
            raise HTTPException(409, '长期记忆最多 50 条，请先删除不再需要的记录')
        row = dict(id=str(uuid4()), owner=owner_identity(ctx), kind=kind, content=content.strip(),
                   source_id=source_id, valid_until=valid_until, created_at=time.time())
        with self.engine.begin() as conn:
            conn.execute(memories.insert().values(**row))
        return row

    def delete_memory(self, ctx, memory_id):
        with self.engine.begin() as conn:
            result = conn.execute(memories.delete().where(memories.c.id == memory_id,
                memories.c.owner == owner_identity(ctx)))
            if not result.rowcount:
                raise HTTPException(404, '记忆不存在')

    def equipment(self, ctx, device_name):
        # Bound values and scope joins, independent of the legacy execute-sql endpoint.
        query = text('''SELECT d.device_name, d.parameter, d.value, d.tolerance, d.last_updated
            FROM device_parameters d JOIN production_lines l ON d.line_id=l.line_id
            JOIN plants p ON l.plant_id=p.plant_id
            WHERE p.tenant_id=:tenant AND p.plant_id=:plant AND d.line_id=:line
            AND d.device_name=:device LIMIT 30''')
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(query, dict(tenant=ctx.tenant_id,
                plant=ctx.plant_id, line=ctx.line_id, device=device_name)).mappings()]

    def faults(self, ctx, fault_code='', device_type='', keyword=''):
        """Fixed read-only query. Tenant-wide fault sharing matches the maintenance UI."""
        if not any(value.strip() for value in (fault_code, device_type, keyword)):
            raise ValueError('Fault lookup requires a code, device or symptom')
        query = 'SELECT id, device_type, code, description, solution, severity FROM fault_knowledge WHERE tenant_id=:tenant'
        params = {'tenant': ctx.tenant_id}
        def pattern(value):
            return '%' + value.strip().replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
        if fault_code.strip():
            query += ' AND UPPER(code)=:code'
            params['code'] = fault_code.strip().upper()
        if device_type.strip():
            query += " AND device_type LIKE :device ESCAPE '!'"
            params['device'] = pattern(device_type)
        if keyword.strip():
            query += " AND (description LIKE :keyword ESCAPE '!' OR solution LIKE :keyword ESCAPE '!')"
            params['keyword'] = pattern(keyword)
        # One extra row lets the workflow refuse an overly broad result instead of silently truncating.
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(text(query + ' ORDER BY device_type, code, id LIMIT 11'), params).mappings()]
