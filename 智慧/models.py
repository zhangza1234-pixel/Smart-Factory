
# models.py
# 可用模型列表，以及获得访问模型的客户端
#     实际使用时可以根据自己的实际情况调整

# 阿里的通义千问大模型
#    官网: https://bailian.console.aliyun.com/#/home
ALI_TONGYI_API_KEY_OS_VAR_NAME = "sk-a961eb668d254d9798a8e3fc9d90c06b"
ALI_TONGYI_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
ALI_TONGYI_MAX_MODEL = "qwen-max-latest"
ALI_TONGYI_TURBO_MODEL = "qwen-turbo-latest"
ALI_TONGYI_DEEPSEEK_R1 = "deepseek-r1"
ALI_TONGYI_DEEPSEEK_R10528 = "deepseek-r1-0528"
ALI_TONGYI_DEEPSEEK_V3 = "deepseek-v3"
ALI_TONGYI_REASONER_MODEL = "qvq-max-latest"
ALI_TONGYI_EMBEDDING_V2 = "text-embedding-v2"
ALI_TONGYI_EMBEDDING_V3 = "text-embedding-v3"
ALI_TONGYI_EMBEDDING_V4 = "text-embedding-v4"

# DeepSeek
#   官网：https://platform.deepseek.com/api_keys
DEEPSEEK_API_KEY_OS_VAR_NAME = "DEEPSEEK_API_KEY"
DEEPSEEK_URL = "https://api.deepseek.com/v1"
DEEPSEEK_CHAT_MODEL = "deepseek-chat"
DEEPSEEK_REASONER_MODEL = "deepseek-reasoner"

# 腾讯混元
'''
#   官网：https://hunyuan.cloud.tencent.com/#/app/modelSquare
TENCENT_HUNYUAN_API_KEY_OS_VAR_NAME = "HUNYUAN_API_KEY"
TENCENT_HUNYUAN_URL = "https://api.hunyuan.cloud.tencent.com/v1"
TENCENT_HUNYUAN_TURBO_MODEL = "hunyuan-turbos-latest"
TENCENT_HUNYUAN_REASONER_MODEL = "hunyuan-t1-latest"
TENCENT_HUNYUAN_LONGCONTEXT_MODEL = "hunyuan-large-longcontext"
# TENCENT_HUNYUAN_EMBEDDING = "hunyuan-embedding"
# TENCENT_SECRET_ID_OS_VAR_NAME = "Tencent_SecretId"
# TENCENT_SECRET_KEY_OS_VAR_NAME = "Tencent_SecretKey"
'''


import os
from langchain_openai import ChatOpenAI
from openai import OpenAI
import inspect
from langchain_community.embeddings import DashScopeEmbeddings, HunyuanEmbeddings


# 使用原生api获得指定平台的客户端 (默认是：阿里通义千问)
def get_normal_client(api_key=os.getenv(ALI_TONGYI_API_KEY_OS_VAR_NAME),
                      base_url=ALI_TONGYI_URL,
                      verbose=False, debug=False):
    """
    使用原生api获得指定平台的客户端，但未指定具体模型，缺省平台为阿里云百炼
    也可以通过传入api_key，base_url两个参数来覆盖默认值
    verbose，debug两个参数，分别控制是否输出调试信息，是否输出详细调试信息，默认不打印
    """
    function_name = inspect.currentframe().f_code.co_name
    if (verbose):
        print(f"{function_name}-平台：{base_url}")
    if (debug):
        print(f"{function_name}-平台：{base_url},key：{api_key}")
    return OpenAI(api_key=api_key, base_url=base_url)



# 通过LangChain获得指定平台和模型的客户端 (默认是：阿里通义千问)
def get_lc_model_client(api_key=os.getenv(ALI_TONGYI_API_KEY_OS_VAR_NAME),
                        base_url=ALI_TONGYI_URL,
                        model=ALI_TONGYI_MAX_MODEL,
                        temperature=0.7, verbose=False, debug=False):
    """
        通过LangChain获得指定平台和模型的客户端，设定的默认平台和模型为阿里百炼qwen
        也可以通过传入api_key，base_url，model三个参数来覆盖默认值
        verbose，debug两个参数，分别控制是否输出调试信息，是否输出详细调试信息，默认不打印
    """
    function_name = inspect.currentframe().f_code.co_name
    if (verbose):
        print(f"{function_name}-平台：{base_url},模型：{model},温度：{temperature}")
    if (debug):
        print(f"{function_name}-平台：{base_url},模型：{model},温度：{temperature},key：{api_key}")
    return ChatOpenAI(api_key=api_key,
                      base_url=base_url,
                      model=model,
                      temperature=temperature,
                      extra_body={"enable_thinking": False})

# 通过LangChain使用 阿里大模型 DEEPSEEK_V3
def get_ali_model_client(model=ALI_TONGYI_DEEPSEEK_V3,
                         temperature=0.7, verbose=False, debug=False):
    """通过LangChain使用阿里大模型DEEPSEEK_V3"""
    return get_lc_model_client(api_key=os.getenv(ALI_TONGYI_API_KEY_OS_VAR_NAME),
                               base_url=ALI_TONGYI_URL,
                               model=model,
                               temperature=temperature,
                               verbose=verbose,
                               debug=debug)


# 通过LangChain使用 DeepSeek大模型
def get_ds_model_client(model=DEEPSEEK_CHAT_MODEL,
                        temperature=0.7, verbose=False, debug=False):
    """通过LangChain使用DeepSeek大模型"""
    return get_lc_model_client(api_key=os.getenv(DEEPSEEK_API_KEY_OS_VAR_NAME),
                               base_url=DEEPSEEK_URL,
                               model=model,
                               temperature=temperature,
                               verbose=verbose,
                               debug=debug)


# 通过LangChain获得一个阿里通义千问嵌入模型的实例
def get_ali_embeddings(model=ALI_TONGYI_EMBEDDING_V4):
    """通过LangChain获得一个阿里通义千问嵌入模型的实例"""
    return DashScopeEmbeddings(
        model=model,
        dashscope_api_key=os.getenv(ALI_TONGYI_API_KEY_OS_VAR_NAME)
    )



# 阿里大模型客户端和嵌入模型的客户端
def get_ali_clients():
    '''
    产生阿里大模型客户端和嵌入模型的客户端
    :return: 阿里大模型客户端和嵌入模型的客户端
    '''
    return get_ali_model_client(), get_ali_embeddings()


