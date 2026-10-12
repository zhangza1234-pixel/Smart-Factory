"""Scoped business records, maintained through forms rather than arbitrary SQL."""
import hashlib
import json
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


class Scope(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=100)
    plant_id: str = Field(min_length=1, max_length=100)
    line_id: str = Field(min_length=1, max_length=100)
    role: Literal['操作员', '工程师', '专家']
    user_id: str = 'demo'


class Browse(BaseModel):
    user_ctx: Scope
    keyword: str = Field(default='', max_length=200)
    device: str = Field(default='', max_length=200)
    severity: Literal['', '低', '中', '高'] = ''
    page: int = Field(default=1, ge=1, le=100000)


class EquipmentFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    device_name: str = Field(min_length=1, max_length=200)
    parameter: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=200)
    tolerance: str = Field(default='', max_length=100)


class FaultFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    device_type: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=10000)
    solution: str = Field(min_length=1, max_length=10000)
    severity: Literal['低', '中', '高']


class Save(BaseModel):
    user_ctx: Scope
    record: EquipmentFields | FaultFields
    id: int | None = Field(default=None, ge=1)
    revision: str = Field(default='', max_length=64)


def revision(row):
    return hashlib.sha256(json.dumps(dict(row), sort_keys=True, default=str,
        ensure_ascii=False).encode()).hexdigest()


def create_router(get_engine):
    router = APIRouter(prefix='/business', tags=['业务数据维护'])
    tables = {'equipment': 'device_parameters', 'faults': 'fault_knowledge'}

    def config(kind, ctx):
        if kind not in tables:
            raise HTTPException(404, '未知业务类型')
        if kind == 'equipment':
            source = ('device_parameters d JOIN production_lines l ON d.line_id=l.line_id '
                      'JOIN plants p ON l.plant_id=p.plant_id')
            scope = 'd.line_id=:line_id AND p.plant_id=:plant_id AND p.tenant_id=:tenant_id'
            fields = ['device_name', 'parameter', 'value', 'tolerance']
        else:
            source, scope = 'fault_knowledge d', 'd.tenant_id=:tenant_id'
            fields = ['device_type', 'code', 'description', 'solution', 'severity']
        return source, scope, fields, ctx.model_dump()

    @router.post('/{kind}/list')
    def browse(kind: str, request: Browse):
        source, scope, fields, params = config(kind, request.user_ctx)
        device_field = fields[0]
        where = scope
        if request.device:
            where += f' AND d.{device_field}=:device'
            params['device'] = request.device
        if request.keyword.strip():
            # Escape LIKE wildcards: a search term is literal text, never SQL.
            params['keyword'] = '%' + request.keyword.strip().replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
            where += ' AND (' + ' OR '.join(f"d.{f} LIKE :keyword ESCAPE '!'" for f in fields) + ')'
        if kind == 'faults' and request.severity:
            where += ' AND d.severity=:severity'
            params['severity'] = request.severity
        try:
            with get_engine().connect() as conn:
                devices = list(conn.execute(text(f'SELECT DISTINCT d.{device_field} FROM {source} WHERE {scope} ORDER BY d.{device_field}'), params).scalars())
                total = conn.execute(text(f'SELECT COUNT(*) FROM {source} WHERE {where}'), params).scalar_one()
                page = min(request.page, max(1, (total + 49) // 50))
                params['offset'] = (page - 1) * 50
                rows = conn.execute(text(f'SELECT d.* FROM {source} WHERE {where} ORDER BY d.{device_field}, d.{fields[1]}, d.id LIMIT 50 OFFSET :offset'), params).mappings().all()
                return {'items': [{**dict(row), 'revision': revision(row)} for row in rows],
                        'total': total, 'devices': devices, 'page': page, 'page_size': 50}
        except SQLAlchemyError:
            logging.getLogger(__name__).exception('Business browse failed')
            raise HTTPException(503, '业务数据库暂不可用，请检查数据库连接。')

    @router.post('/{kind}/save')
    def save(kind: str, request: Save):
        source, scope, fields, params = config(kind, request.user_ctx)
        if request.user_ctx.role == '操作员':
            raise HTTPException(403, '操作员只能查看，请联系工程师或专家维护。')
        expected = EquipmentFields if kind == 'equipment' else FaultFields
        if not isinstance(request.record, expected):
            raise HTTPException(422, '表单类型不匹配')
        values = request.record.model_dump()
        try:
            engine = get_engine()
            with engine.begin() as conn:
                valid = conn.execute(text('SELECT l.line_id FROM production_lines l JOIN plants p ON l.plant_id=p.plant_id '
                    'WHERE l.line_id=:line_id AND p.plant_id=:plant_id AND p.tenant_id=:tenant_id'), params).first()
                if not valid:
                    raise HTTPException(403, '当前租户、工厂和产线不匹配。')
                if request.id is not None:
                    params['id'] = request.id
                    lock = ' FOR UPDATE' if engine.dialect.name == 'mysql' else ''
                    old = conn.execute(text(f'SELECT d.* FROM {source} WHERE {scope} AND d.id=:id' + lock), params).mappings().first()
                    if old is None:
                        raise HTTPException(404, '记录不存在或不在当前访问范围内。')
                    if not request.revision or revision(old) != request.revision:
                        raise HTTPException(409, '记录已被其他人修改，请刷新页面后重新编辑。')
                    conn.execute(text(f'UPDATE {tables[kind]} SET ' + ', '.join(f'{f}=:{f}' for f in fields) + ' WHERE id=:id'), {**values, 'id': request.id})
                    record_id = request.id
                else:
                    owner = 'line_id' if kind == 'equipment' else 'tenant_id'
                    values[owner] = params[owner]
                    columns = [*fields, owner]
                    result = conn.execute(text(f'INSERT INTO {tables[kind]} (' + ', '.join(columns) + ') VALUES (' + ', '.join(':' + f for f in columns) + ')'), values)
                    record_id = result.lastrowid
            return {'id': record_id, 'message': '已保存到 MySQL'}
        except IntegrityError:
            raise HTTPException(409, '同一设备的参数名称或故障代码已存在，请在“修改记录”中维护。')
        except SQLAlchemyError:
            logging.getLogger(__name__).exception('Business save failed')
            raise HTTPException(503, '保存失败，未提交修改；请检查数据库连接和账户写入权限。')

    return router
