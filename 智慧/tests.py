from dotenv import load_dotenv
import os
from dashscope import TextEmbedding

load_dotenv()

api_key = os.getenv("DASHSCOPE_API_KEY")
if not api_key:
    print("❌ DASHSCOPE_API_KEY 未设置！")
    exit(1)

try:
    response = TextEmbedding.call(
        model='text-embedding-v3',
        input=['测试文本'],
        api_key=api_key
    )
    if response.status_code == 200:
        print("✅ DashScope 连接成功！")
        print("向量维度:", len(response.output['embeddings'][0]['embedding']))
    else:
        print(f"❌ API 返回错误: {response.code} - {response.message}")
except Exception as e:
    print(f"❌ 网络或认证失败: {str(e)}")