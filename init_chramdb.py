# 定义和创建向量数据库存储
import uvicorn
from langchain_chroma import Chroma
from langchain_community .embeddings import DashScopeEmbeddings
import os
from dotenv import load_dotenv
load_dotenv()


def init_chromadb():
    # 1.创建嵌入模型
    llm_embeddings = DashScopeEmbeddings(model='text-embedding-v3')

    # 2.创建 数据库集合
    chroma_collection =  Chroma(
        collection_name='factory_knowledge',
        embedding_function=llm_embeddings,
        persist_directory='./chroma_db2'
    )
    # 3.添加文档(测试数据)documents,metadatas,ids 一一对应
    documents = [
        "2：385.5±2°C，适用于电池模组焊接",
        "电芯封装真空度要求：≤0.01Pa，保持时间>30s",
        "F0012故障解决方案：检查保护气体纯度，校准聚焦镜位置",
        "液压系统压力测试标准：35.0±0.5MPa，保压5分钟",
        "SMT贴片工艺要求：锡膏厚度0.12±0.01mm，贴片精度±0.025mm",
        "激光焊接机：电源为 380V/50HZ，额定功率 120KW，焊接重复精度≤±0.01mm，整机重量 5.5T，机身尺寸 L5100W3000H2600mm"
    ]
    metadatas = [
        {"tenant_id": "tenant_A", "plant_id": "plant_A1", "doc_type": "standard", "access_level": "工程师"},
        {"tenant_id": "tenant_A", "plant_id": "plant_A1", "doc_type": "spec", "access_level": "专家"},
        {"tenant_id": "tenant_A", "plant_id": "plant_A1", "doc_type": "solution", "access_level": "操作员"},
        {"tenant_id": "tenant_B", "plant_id": "plant_B1", "doc_type": "standard", "access_level": "工程师"},
        {"tenant_id": "tenant_C", "plant_id": "plant_C1", "doc_type": "spec", "access_level": "专家"},
        {"tenant_id": "tenant_A", "plant_id": "plant_A1", "doc_type": "spec", "access_level": "专家"}
    ]
    ids = ['doc1', 'doc2', 'doc3', 'doc4', 'doc5', 'doc6']
    chroma_collection.add_texts(
        texts=documents,
        metadatas=metadatas,
        ids=ids
    )
    print("向量数据库创建成功！")
    return chroma_collection
if __name__ == '__main__':
    init_chromadb()