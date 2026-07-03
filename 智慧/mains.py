# main.py
import json
import os
import time
from typing import Dict, List
from datetime import datetime

import pymysql
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from langchain.agents import create_openai_tools_agent, AgentExecutor
from langchain_community.document_loaders import (
    PyPDFLoader, TextLoader, Docx2txtLoader, UnstructuredPDFLoader
)
from langchain_community.tools import Tool
from langchain_community.utilities import SQLDatabase
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
import tempfile
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
    'host': '127.0.0.1',
    'port': 3306,
    'user': 'root',
    'password': 'root',
    'database': 'factory_kb',
    'charset': 'utf8mb4'
}

# 创建 FastAPI 应用
app = FastAPI(
    title='智能工厂助手后端',
    description='提供知识查询、SQL执行、文档上传和系统状态查询API服务',
    version='1.0'
)

# ----------------------------
# 2. 数据模型定义 (Pydantic)
# ----------------------------
class UserContext(BaseModel):
    tenant_id: str
    plant_id: str
    line_id: str
    role: str

class QueryRequest(BaseModel):
    question: str
    user_ctx: UserContext

class EquipmentRequest(BaseModel):
    device_name: str
    user_ctx: UserContext

class SqlRequest(BaseModel):
    sql: str
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

if not test_db_connection():
    raise RuntimeError('无法连接到 MySQL 数据库，请检查配置')

def execute_sql(sql: str, user_ctx: UserContext) -> List[Dict]:
    conn = None
    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute(sql)
            return cursor.fetchall()
    except Exception as e:
        logger.error(f'SQL 执行失败: {e}')
        raise HTTPException(status_code=500, detail='SQL 执行失败')
    finally:
        if conn:
            conn.close()

def process_upload_file(file: UploadFile):
    """处理上传文件，返回分块文本列表"""
    suffix = f".{file.filename.split('.')[-1]}" if '.' in file.filename else ""
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = file.file.read()
        tmp.write(content)
        temp_path = tmp.name

    file_ext = file.filename.split('.')[-1].lower()
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    documents = []

    try:
        if file_ext == 'pdf':
            loader = PyPDFLoader(temp_path)
        elif file_ext == 'txt':
            try:
                loader = TextLoader(temp_path, encoding='utf-8')
            except:
                loader = TextLoader(temp_path, encoding='gbk')
        elif file_ext in ['doc', 'docx']:
            loader = Docx2txtLoader(temp_path)
        else:
            raise ValueError(f'不支持的文件类型: {file_ext}')

        docs = loader.load()
        documents = text_splitter.split_documents(docs)
        return [doc.page_content for doc in documents]

    except Exception as e:
        logger.error(f'主加载器失败: {e}')
        if file_ext == 'pdf':
            try:
                loader = UnstructuredPDFLoader(temp_path)
                docs = loader.load()
                documents = text_splitter.split_documents(docs)
                return [doc.page_content for doc in documents]
            except Exception as e2:
                logger.error(f'备用PDF加载器失败: {e2}')
        raise HTTPException(status_code=500, detail='文件处理失败')
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

# ----------------------------
# 4. 向量数据库 & LangChain Agent
# ----------------------------
chroma_collection = None

def init_chroma():
    global chroma_collection
    if chroma_collection is None:
        from init_chramdb import init_chromadb  # 确保该模块存在
        chroma_collection = init_chromadb()
    return chroma_collection

agent_executor = None

def init_langchain_agent():
    global chroma_collection
    db_uri = f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}/{DB_CONFIG['database']}"
    db = SQLDatabase.from_uri(db_uri)

    DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
    if not DASHSCOPE_API_KEY:
        raise ValueError("DASHSCOPE_API_KEY 未在 .env 中设置")

    llm = ChatOpenAI(
        api_key=DASHSCOPE_API_KEY,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_name="qwen-max",
        temperature=0.1,
        top_p=0.9
    )

    chroma_collection = init_chroma()

    def sql_tool(query: str) -> str:
        try:
            result = db.run(query)
            return result if result else "查询结果为空"
        except Exception as e:
            logger.error(f"SQL工具错误: {e}")
            return f"SQL执行错误: {str(e)}"

    def chroma_search_tool(query: str) -> str:
        results = chroma_collection.similarity_search(query, k=1)
        texts = [doc.page_content for doc in results]
        return json.dumps(texts, ensure_ascii=False)

    tools = [
        Tool(
            name="chroma_search",
            func=chroma_search_tool,
            description="用于检索技术文档、操作指南、故障手册等非结构化内容。"
        ),
        Tool(
            name="sql_db_query",
            func=sql_tool,
            description="用于查询结构化数据（如设备参数、租户信息等）。"
        )
    ]

    prompt = ChatPromptTemplate.from_messages([
        ("system", """
你是一个智能工厂知识助手。
用户角色：{role}，租户：{tenant_id}，工厂：{plant_id}，产线：{line_id}。

**规则**：
1. 优先使用 chroma_search 查技术文档；
2. 若无结果或需结构化数据，再用 sql_db_query；
3. 所有 SQL 必须带 tenant_id 或 line_id 过滤。

表结构参考：
- tenants (tenant_id, name)
- plants (plant_id, tenant_id)
- production_lines (line_id, plant_id)
- device_parameters (line_id, device_name, parameter, value...)
- fault_knowledge (tenant_id, code, description, solution...)

请给出准确、简洁的回答。
        """),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}")
    ])

    agent = create_openai_tools_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True)

# 初始化 Agent（启动时）
try:
    agent_executor = init_langchain_agent()
    logger.info("LangChain Agent 初始化成功")
except Exception as e:
    logger.error(f"LangChain Agent 初始化失败: {e}")
    agent_executor = None

# ----------------------------
# 5. API 路由
# ----------------------------

@app.post('/test_file-upload')
async def test_file_upload(file: UploadFile = File(...)):
    try:
        chunks = process_upload_file(file)
        return {
            'status': 'success',
            'filename': file.filename,
            'chunk_count': len(chunks),
            'sample_chunk': chunks[0][:200] + '...' if chunks else ''
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'文件处理失败: {e}')

@app.post('/query')
async def query_knowledge(request: QueryRequest):
    if agent_executor is None:
        raise HTTPException(status_code=503, detail='Agent 未初始化')

    try:
        result = agent_executor.invoke({
            "input": request.question,
            "tenant_id": request.user_ctx.tenant_id,
            "plant_id": request.user_ctx.plant_id,
            "line_id": request.user_ctx.line_id,
            "role": request.user_ctx.role
        })
        return {"answer": result["output"]}
    except Exception as e:
        logger.error(f"查询失败: {e}")
        raise HTTPException(status_code=500, detail='查询失败')

@app.post('/query-equipment')
async def query_equipment(request: EquipmentRequest):
    logger.info(f"查询设备参数: {request.device_name} on line {request.user_ctx.line_id}")
    try:
        # ⚠️ 注意：此处有 SQL 注入风险！应使用参数化查询
        if request.device_name:
            sql = f"""
                SELECT device_name, parameter, value, tolerance, last_updated 
                FROM device_parameters 
                WHERE line_id = '{request.user_ctx.line_id}' 
                AND device_name = '{request.device_name}'
            """
        else:
            sql = f"""
                SELECT device_name, parameter, value, tolerance, last_updated
                FROM device_parameters
                WHERE line_id = '{request.user_ctx.line_id}'
            """
        results = execute_sql(sql, request.user_ctx)
        return results
    except Exception as e:
        logger.error(f"设备参数查询失败: {e}")
        raise HTTPException(status_code=500, detail='查询设备参数失败')

@app.post('/execute-sql')
async def execute_sql_query(request: SqlRequest):
    logger.info(f"执行SQL: {request.sql}")
    try:
        results = execute_sql(request.sql, request.user_ctx)
        return results
    except Exception as e:
        logger.error(f"SQL执行失败: {e}")
        raise HTTPException(status_code=500, detail='执行SQL失败')

@app.post('/upload-document')
async def upload_document(
    file: UploadFile = File(...),
    user_ctx: str = Form(...),
    title: str = Form(...),
    category: str = Form(...),
    access_level: str = Form(...)
):
    try:
        logger.info(f"上传文档: {file.filename}")
        user_context = json.loads(user_ctx)
        chunks = process_upload_file(file)

        metadatas = [{
            "tenant_id": user_context["tenant_id"],
            "plant_id": user_context["plant_id"],
            "line_id": user_context.get("line_id", ""),
            "doc_type": "uploaded",
            "access_level": access_level,
            "title": title,
            "category": category
        } for _ in chunks]

        doc_id_base = f"upload_{int(time.time())}"
        ids = [f"{doc_id_base}_{i}" for i in range(len(chunks))]

        chroma = init_chroma()
        chroma.add_texts(texts=chunks, metadatas=metadatas, ids=ids)

        logger.info(f"成功添加 {len(chunks)} 个文档块到向量库")
        return {
            "status": "success",
            "filename": file.filename,
            "chunks": len(chunks)
        }
    except Exception as e:
        logger.error(f"文档上传失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return JSONResponse({"status": "ok", "service": "智能工厂知识助手后端"})

@app.get("/db-status")
async def db_status():
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

@app.get("/vector-status")
async def vector_status():
    try:
        chroma = init_chroma()
        count = chroma._client.get_collection("factory_knowledge").count()
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