# main.py
import os
from pathlib import PurePosixPath
from typing import Dict, List
from datetime import datetime, date
from uuid import UUID

import pymysql
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, model_validator
from typing import Literal
from threading import RLock
from ingestion import load_chunks, prepare_chunks
from rag import HybridRetriever, RAGService, RAGSettings, ROLE_LEVELS
from state_store import StateStore, family_identity
from workflow import ConversationRAG, query_dates, TemporalQueryError
from business import create_router
from intent import direct_intent, direct_query
from langchain_openai import ChatOpenAI
import logging
from dotenv import load_dotenv

# ----------------------------
# 1. 初始化配置
# ----------------------------
load_dotenv()  # 加载 .env 文件中的环境变量

# 配置日志
log_filename = f'main_{datetime.now().strftime("%Y-%m-%d")}.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename=log_filename,
    filemode='a'
)
logger = logging.getLogger('factory_kb')

# 数据库配置
DB_CONFIG = {
    'host': os.getenv('MYSQL_HOST', '127.0.0.1'),
    'port': int(os.getenv('MYSQL_PORT', '3306')),
    'user': os.getenv('MYSQL_USER', 'factory_app'),
    'password': os.getenv('MYSQL_PASSWORD', ''),
    'database': os.getenv('MYSQL_DATABASE', 'factory_kb'),
    'charset': 'utf8mb4',
    'connect_timeout': 5, 'read_timeout': 30, 'write_timeout': 30
}

# 创建 FastAPI 应用
app = FastAPI(
    title='智能工厂助手后端',
    description='提供知识查询、SQL执行、文档上传和系统状态查询API服务',
    version='2.0'
)

# ----------------------------
# 2. 数据模型定义 (Pydantic)
# ----------------------------
class UserContext(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=100)
    plant_id: str = Field(min_length=1, max_length=100)
    line_id: str = Field(min_length=1, max_length=100)
    role: Literal["操作员", "工程师", "专家"]
    user_id: str = Field(default='demo', min_length=1, max_length=100)

class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    user_ctx: UserContext
    top_k: int = Field(default=5, ge=1, le=10)
    session_id: UUID | None = None
    as_of: date | None = None
    comparison_dates: List[date] | None = Field(default=None, min_length=2, max_length=2)

    @model_validator(mode='after')
    def validate_dates(self):
        if self.as_of and self.comparison_dates:
            raise ValueError('指定日期和版本对比不能同时使用')
        if self.comparison_dates and len(set(self.comparison_dates)) != 2:
            raise ValueError('请选择两个不同的对比日期')
        return self


class ContextRequest(BaseModel):
    user_ctx: UserContext


class VersionStatusRequest(ContextRequest):
    status: Literal['published', 'withdrawn']


class MemoryRequest(ContextRequest):
    kind: Literal['preference', 'background', 'reviewed_experience'] = 'preference'
    content: str = Field(min_length=1, max_length=1000, pattern=r'\S')
    source_id: str = Field(default='', max_length=64)
    valid_until: date | None = None

class EquipmentRequest(BaseModel):
    device_name: str
    user_ctx: UserContext

class FaultRequest(BaseModel):
    device_type: str = Field(default='', max_length=200)
    fault_code: str = Field(default='', max_length=100)
    user_ctx: UserContext

# ----------------------------
# 3. 工具函数
# ----------------------------
def test_db_connection():
    try:
        conn = pymysql.connect(**DB_CONFIG)
        conn.close()
        return True
    except Exception as e:
        logger.error(f'数据库连接失败: {e}')
        return False

# MySQL is optional for document RAG.

def execute_sql(sql: str, user_ctx: UserContext, params=None) -> List[Dict]:
    conn = None
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    except Exception as e:
        logger.error(f'SQL 执行失败: {e}')
        raise HTTPException(status_code=500, detail='SQL 执行失败')
    finally:
        if conn:
            conn.close()

def configured_chunks(file, strategy=None):
    return load_chunks(file, chunk_size=int(os.getenv("RAG_CHUNK_SIZE", "800")),
        chunk_overlap=int(os.getenv("RAG_CHUNK_OVERLAP", "120")),
        max_bytes=int(os.getenv("UPLOAD_MAX_MB", "20")) * 1024 * 1024,
        strategy=strategy or os.getenv("RAG_SPLIT_STRATEGY", "structured"))


def process_upload_file(file: UploadFile):
    chunks, _ = configured_chunks(file)
    return [chunk.page_content for chunk in chunks]


chroma_collection = None
rag_service = None
initialization_lock = RLock()
state_store = None


def init_state():
    global state_store
    with initialization_lock:
        if state_store is None:
            state_store = StateStore()
        return state_store


app.include_router(create_router(lambda: init_state().engine))


def init_chroma():
    global chroma_collection
    with initialization_lock:
        if chroma_collection is None:
            from init_chramdb import init_chromadb
            chroma_collection = init_chromadb()
        return chroma_collection


def init_rag():
    global rag_service
    with initialization_lock:
        if rag_service is None:
            key = os.getenv("DASHSCOPE_API_KEY")
            if not key:
                raise ValueError("DASHSCOPE_API_KEY 未配置")
            settings = RAGSettings(candidate_k=int(os.getenv("RAG_CANDIDATE_K", "20")),
                max_distance=float(os.getenv("RAG_MAX_DISTANCE", "1.2")),
                context_chars=int(os.getenv("RAG_CONTEXT_CHARS", "10000")),
                cache_seconds=int(os.getenv("RAG_CACHE_SECONDS", "60")))
            llm = ChatOpenAI(api_key=key,
                base_url=os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                model=os.getenv("LLM_MODEL", "qwen-max"), temperature=0.1, timeout=45,
                max_retries=1, max_tokens=2500)
            rag_service = ConversationRAG(HybridRetriever(None, settings, store_factory=init_chroma), llm, init_state())
        return rag_service


# ----------------------------
# 5. API 路由
# ----------------------------

@app.post('/test_file-upload')
def test_file_upload(file: UploadFile = File(...),
    split_strategy: Literal["structured", "recursive"] | None = Form(None)):
    try:
        chunks, _ = configured_chunks(file, split_strategy)
        return {
            'status': 'success',
            'filename': file.filename,
            'chunk_count': len(chunks),
            'sample_chunk': chunks[0].page_content[:200] if chunks else '',
            'preview': [{'text': chunk.page_content, 'metadata': chunk.metadata} for chunk in chunks[:10]]
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "文件处理失败")

@app.post('/query')
def query_knowledge(request: QueryRequest):
    try:
        if direct_intent(request.question):
            return direct_query(init_state(), request.question.strip(), request.user_ctx,
                str(request.session_id) if request.session_id else None)
        query_dates(request.question, request.as_of, request.comparison_dates)
        return init_rag().query(request.question.strip(), request.user_ctx, request.top_k,
            str(request.session_id) if request.session_id else None, request.as_of, request.comparison_dates)
    except HTTPException:
        raise
    except TemporalQueryError as exc:
        raise HTTPException(422, str(exc))
    except ValueError:
        raise HTTPException(503, '知识服务配置无效')
    except Exception:
        logger.exception("知识查询失败")
        raise HTTPException(503, '知识服务暂不可用，请检查MySQL、模型配置和向量库')


@app.post('/retrieve')
def retrieve_knowledge(request: QueryRequest):
    try:
        all_sources = []
        diagnostics = []
        for day in query_dates(request.question, request.as_of, request.comparison_dates):
            sources, diag = init_rag().retriever.retrieve(request.question.strip(), request.user_ctx, request.top_k,
                eligible=init_state().selected_versions(request.user_ctx, day))
            for source in sources:
                all_sources.append({**source, 'as_of': day.isoformat(), 'citation': f'S{len(all_sources) + 1}'})
            diagnostics.append(diag)
        return {"sources": all_sources, "diagnostics": diagnostics}
    except HTTPException:
        raise
    except TemporalQueryError as exc:
        raise HTTPException(422, str(exc))
    except Exception:
        logger.exception("检索失败")
        raise HTTPException(503, '检索服务暂不可用')


@app.post('/query-equipment')
def query_equipment(request: EquipmentRequest):
    logger.info(f"查询设备参数: {request.device_name} on line {request.user_ctx.line_id}")
    try:
        sql = """SELECT d.device_name, d.parameter, d.value, d.tolerance, d.last_updated
                 FROM device_parameters d JOIN production_lines l ON d.line_id=l.line_id
                 JOIN plants p ON l.plant_id=p.plant_id
                 WHERE d.line_id=%s AND p.plant_id=%s AND p.tenant_id=%s"""
        params = [request.user_ctx.line_id, request.user_ctx.plant_id, request.user_ctx.tenant_id]
        if request.device_name:
            sql += " AND d.device_name = %s"
            params.append(request.device_name)
        results = execute_sql(sql, request.user_ctx, params)
        return results
    except Exception as e:
        logger.error(f"设备参数查询失败: {e}")
        raise HTTPException(status_code=500, detail='查询设备参数失败')

@app.post('/execute-sql')
def execute_sql_query():
    raise HTTPException(410, '自由SQL接口已停用，请使用故障、设备或文档查询接口')


@app.post('/query-faults')
def query_faults(request: FaultRequest):
    sql = 'SELECT device_type, code, description, solution, severity FROM fault_knowledge WHERE tenant_id=%s'
    params = [request.user_ctx.tenant_id]
    if request.device_type:
        sql += ' AND device_type LIKE %s'
        params.append('%' + request.device_type + '%')
    if request.fault_code:
        sql += ' AND code LIKE %s'
        params.append('%' + request.fault_code + '%')
    return execute_sql(sql + ' LIMIT 100', request.user_ctx, params)

@app.post('/upload-document')
def upload_document(file: UploadFile = File(...), user_ctx: str = Form(...),
    title: str = Form("", max_length=200),
    category: str = Form(..., min_length=1, max_length=100), access_level: str = Form(...),
    split_strategy: Literal["structured", "recursive"] | None = Form(None),
    family_name: str = Form('', max_length=200), version: str = Form('1', max_length=80),
    effective_from: date | None = Form(None), effective_to: date | None = Form(None),
    publish: bool = Form(True)):
    try:
        context = UserContext.model_validate_json(user_ctx)
    except ValidationError:
        raise HTTPException(422, "用户上下文格式无效")
    if context.role != "专家":
        raise HTTPException(403, "只有专家可以上传文档")
    if access_level not in ROLE_LEVELS or not category.strip():
        raise HTTPException(422, "请提供有效的分类和访问级别")
    resolved_title = title.strip() or PurePosixPath((file.filename or "").replace("\\", "/")).stem.strip()[:200] or "未命名文档"
    start = effective_from or date.today()
    if effective_to and effective_to <= start:
        raise HTTPException(422, '失效日期必须晚于生效日期（失效当天起不适用）')
    if not version.strip():
        raise HTTPException(422, '版本号不能为空')
    name = family_name.strip() or resolved_title
    chunks, digest = configured_chunks(file, split_strategy)
    metadata = {**context.model_dump(exclude={"role", "user_id"}), "doc_type": "uploaded",
                "title": resolved_title, "category": category.strip(), "access_level": access_level,
                "family_name": name, "family_id": family_identity(context, name), 'version': version.strip(),
                'effective_from': start.isoformat(), 'effective_to': effective_to.isoformat() if effective_to else '',
                'original_filename': PurePosixPath((file.filename or '').replace('\\', '/')).name[:300]}
    document_id, ids, texts, metadatas = prepare_chunks(chunks, digest, metadata)
    try:
        catalog = init_state()
        catalog.stage(document_id, context, metadata, len(chunks))
        chroma = init_chroma()
        existing = set()
        for offset in range(0, len(ids), 100):
            existing.update(chroma.get(ids=ids[offset:offset + 100], include=[])['ids'])
        pending = [i for i, uid in enumerate(ids) if uid not in existing]
        for offset in range(0, len(pending), 100):
            batch = pending[offset:offset + 100]
            chroma.add_texts(texts=[texts[i] for i in batch],
                             metadatas=[metadatas[i] for i in batch], ids=[ids[i] for i in batch])
        if rag_service is not None:
            rag_service.retriever.invalidate()
        status = catalog.change_status(document_id, context, 'published')['status'] if publish else next(
            row['status'] for row in catalog.catalog(context) if row['id'] == document_id)
        return {"status": "success", "filename": file.filename, "title": resolved_title, "document_id": document_id,
                "chunks": len(chunks), "added_chunks": len(pending), "duplicate": not pending,
                'version': version.strip(), 'version_status': status, 'family_name': name,
                'effective_from': start.isoformat()}
    except HTTPException:
        raise
    except Exception:
        logger.exception("文档入库失败")
        raise HTTPException(503, "文档入库失败，请检查嵌入模型配置和向量库")


@app.post('/documents/list')
def list_documents(request: ContextRequest):
    from rag import authorized
    return {'documents': [row for row in init_state().catalog(request.user_ctx) if authorized(row, request.user_ctx)],
            'current_ids': list(init_state().selected_versions(request.user_ctx, date.today()))}


@app.post('/documents/{document_id}/status')
def change_document_status(document_id: str, request: VersionStatusRequest):
    ctx = request.user_ctx
    rows = [row for row in init_state().catalog(ctx) if row['id'] == document_id]
    if ctx.role != '专家':
        raise HTTPException(403, '只有专家可以发布或撤回版本')
    if not rows:
        raise HTTPException(404, '文档不存在')
    if request.status == 'published':
        expected = [f'{document_id}:{i}' for i in range(rows[0]['chunk_count'])]
        for offset in range(0, len(expected), 100):
            batch = expected[offset:offset + 100]
            if set(init_chroma().get(ids=batch, include=[])['ids']) != set(batch):
                raise HTTPException(409, '文档向量未完整入库，不能发布；请重新上传后重试')
    result = init_state().change_status(document_id, ctx, request.status)
    if rag_service is not None:
        rag_service.retriever.invalidate()
    return result


@app.post('/sessions/{session_id}/history')
def session_history(session_id: UUID, request: ContextRequest):
    return init_state().history(request.user_ctx, str(session_id))


@app.post('/sessions/{session_id}/clear')
def clear_session(session_id: UUID, request: ContextRequest):
    init_state().clear_session(request.user_ctx, str(session_id))
    return {'status': 'cleared'}


@app.post('/memories/list')
def list_memories(request: ContextRequest):
    return {'memories': [{key: value for key, value in row.items() if key != 'owner'}
                         for row in init_state().list_memories(request.user_ctx)]}


@app.post('/memories')
def save_memory(request: MemoryRequest):
    row = init_state().save_memory(request.user_ctx, request.kind, request.content, request.source_id,
                                  request.valid_until.isoformat() if request.valid_until else '')
    return {key: value for key, value in row.items() if key != 'owner'}


@app.post('/memories/{memory_id}/delete')
def delete_memory(memory_id: UUID, request: ContextRequest):
    init_state().delete_memory(request.user_ctx, str(memory_id))
    return {'status': 'deleted'}


@app.get('/memory-status')
def memory_status():
    state = init_state()
    return {'persistence': 'mysql', 'redis': state.refresh_cache_status(), 'ttl_seconds': state.ttl}


@app.get("/health")
async def health_check():
    return JSONResponse({"status": "ok", "service": "智能工厂知识助手后端"})

@app.get("/db-status")
def db_status():
    conn = None
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM tenants")
            tenants = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM device_parameters")
            parameters = cursor.fetchone()[0]
        return {"status": "connected", "tenants": tenants, "parameters": parameters}
    except Exception as e:
        logger.error(f"数据库状态检查失败: {e}")
        raise HTTPException(status_code=500, detail='数据库连接失败')
    finally:
        if conn:
            conn.close()

@app.get("/vector-status")
def vector_status():
    try:
        chroma = init_chroma()
        count = len(chroma.get(include=[])["ids"])
        return {"status": "connected", "documents": count}
    except Exception as e:
        logger.error(f"向量库状态检查失败: {e}")
        raise HTTPException(status_code=500, detail='向量数据库连接失败')

# ----------------------------
# 6. 启动入口
# ----------------------------
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8000)
