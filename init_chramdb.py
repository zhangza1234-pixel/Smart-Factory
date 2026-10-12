"""Open storage without injecting demo records on startup."""
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()


def init_chromadb():
    from langchain_chroma import Chroma
    from langchain_community.embeddings import DashScopeEmbeddings
    from chromadb.config import Settings
    key = os.getenv("DASHSCOPE_API_KEY")
    if not key:
        raise ValueError("请设置 DASHSCOPE_API_KEY")
    embeddings = DashScopeEmbeddings(model=os.getenv("EMBEDDING_MODEL", "text-embedding-v3"), dashscope_api_key=key)
    return Chroma(collection_name=os.getenv("CHROMA_COLLECTION", "factory_knowledge"),
        embedding_function=embeddings,
        client_settings=Settings(anonymized_telemetry=False),
        persist_directory=os.getenv("CHROMA_DIRECTORY", str(Path(__file__).parent / "chroma_db2")))


if __name__ == "__main__":
    init_chromadb()
    print("向量数据库已就绪，请上传真实文档。")
