
# app_frontend.py
import streamlit as st
import requests
import json
import time
import os
from datetime import date, timedelta
from uuid import uuid4, UUID
from streamlit_option_menu import option_menu

# 页面设置
st.set_page_config(
    page_title="智能工厂知识助手",
    page_icon="🏭",
    layout="wide"
)

# 后端API配置
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")


def post_backend(url, **kwargs):
    """Let each control show its ordinary failure message if the backend is offline."""
    try:
        return requests.post(url, **kwargs)
    except requests.RequestException:
        response = requests.Response()
        response.status_code = 503
        response.url = url
        response._content = json.dumps({'detail': '后端服务暂不可用，请检查服务状态。'}, ensure_ascii=False).encode('utf-8')
        response.encoding = 'utf-8'
        return response

# 初始化会话状态
if 'user_ctx' not in st.session_state:
    st.session_state.user_ctx = None
if 'chat_history' not in st.session_state:
    st.session_state.chat_history = []
if 'selected_tab' not in st.session_state:
    st.session_state.selected_tab = "知识助手"
if 'conversation_id' not in st.session_state:
    st.session_state.conversation_id = str(uuid4())


def render_sources(message):
    sources = message.get("sources", [])
    if sources:
        with st.expander(f"查看引用原文（{len(sources)} 个片段）"):
            for source in sources:
                metadata = source["metadata"]
                page = metadata.get("page", 0)
                location = f" · 第 {page} 页" if page else ""
                st.markdown(f"**[{source['citation']}] {metadata.get('title', '未命名文档')}**{location}")
                if metadata.get('doc_type') in ('fault', 'equipment'):
                    st.caption('来源：MySQL 故障库（当前租户共享）' if metadata['doc_type'] == 'fault' else '来源：MySQL 设备参数')
                    st.caption(f"数据：当前数据库快照 · 查询日期：{source.get('as_of', '当前')}")
                else:
                    st.caption(f"文件：{metadata.get('filename', '未知')} · 分类：{metadata.get('category', '未分类')}")
                    st.caption(f"版本：{metadata.get('version', '未登记')} · 查询日期：{source.get('as_of', '当前')} · 生效：{metadata.get('effective_from', '未知')}")
                if metadata.get('section_path'):
                    st.caption(f"章节：{metadata['section_path']}")
                if metadata.get('doc_type') == 'fault':
                    fault = json.loads(source['text'])
                    st.caption(f"设备：{fault['device_type']} · 故障代码：{fault['code']} · 严重程度：{fault['severity']}")
                    st.markdown('**故障现象**')
                    st.text(fault['description'])
                    st.markdown('**处理步骤与注意事项**')
                    st.text(fault['solution'])
                else:
                    st.text(source["text"])
    diagnostics = message.get("diagnostics", {})
    confidence = message.get('confidence', {})
    if confidence:
        st.caption(f"证据判断：{confidence.get('label', '无法判断')}（规则及模型核验，不代表正确概率）")
        for reason in confidence.get('reasons', []):
            st.caption(reason)
    steps = message.get('retrieval_steps', [])
    if steps:
        with st.expander('查看多跳检索过程'):
            for step in steps:
                st.write(f"{step['as_of']} · 第 {step['step']} 步：{step['query']}")
                st.caption(f"前置步骤：{step['depends_on']} · 证据数：{step['source_count']} · {step['missing'] or '完成'}")
                tools = {'faults': 'SQL 故障库', 'equipment': 'SQL 设备参数', 'documents': '文档知识库'}
                st.caption('查询工具：' + tools.get(step['tool'], step['tool']) +
                    ('；SQL 无匹配后补查文档知识库' if step.get('fallback_tool') == 'documents' else ''))
    if diagnostics.get('route') in ('chat', 'history'):
        st.caption('对话回顾：读取当前会话，未检索知识库。' if diagnostics['route'] == 'history' else '日常对话，未检索知识库。')
    elif 'retrieval_ms' in diagnostics:
        st.caption(f"召回候选 {diagnostics.get('candidates', 0)} 个 · 检索 {diagnostics.get('retrieval_ms', 0)} ms · 总耗时 {diagnostics.get('total_ms', 0)} ms")


def knowledge_assistant():
    st.header("🏭 智能工厂知识助手")
    st.caption("支持技术文档、操作规范和故障代码查询，回答附带可查看的原文引用。")
    with st.expander("检索设置"):
        top_k = st.slider("最多引用片段数", min_value=1, max_value=10, value=5)
        time_mode = st.radio('标准适用时间', ['自动识别（默认现行）', '指定日期', '对比两个日期'], horizontal=True)
        as_of, comparison_dates = None, None
        if time_mode == '指定日期':
            as_of = st.date_input('适用日期', value=date.today()).isoformat()
        elif time_mode == '对比两个日期':
            first = st.date_input('对比日期一', value=date(date.today().year - 1, 12, 31))
            second = st.date_input('对比日期二', value=date.today())
            comparison_dates = [first.isoformat(), second.isoformat()]
        st.caption('只写年份时，往年按当年12月31日查询；今年按今天查询。更精确的适用范围请指定日期。')
    with st.expander('会话与长期记忆'):
        st.caption(f"当前会话 ID：{st.session_state.conversation_id}")
        restore_id = st.text_input('恢复会话 ID', placeholder='输入此前保存的会话 ID')
        if st.button('恢复会话'):
            try:
                sid = str(UUID(restore_id))
                response = post_backend(f'{BACKEND_URL}/sessions/{sid}/history',
                    json={'user_ctx': st.session_state.user_ctx}, timeout=15)
                response.raise_for_status()
                history = response.json()
                st.session_state.conversation_id = sid
                st.session_state.chat_history = [{'role': item['role'], 'content': item['content']}
                    for item in history['messages']]
                if history['summary']:
                    st.session_state.chat_history.insert(0, {'role': 'assistant',
                        'content': '较早对话摘要（仅用于理解上下文，技术结论需重新核对）：\n' +
                        json.dumps(history['summary'], ensure_ascii=False)})
                st.rerun()
            except (ValueError, requests.RequestException):
                st.error('无法恢复会话，请检查 ID、当前用户和服务状态。')
        memory_kind = st.selectbox('记忆类型', ['回答偏好', '用户背景', '审核经验'])
        memory_content = st.text_area('明确保存的长期记忆', max_chars=1000,
            help='不会自动把模型回答存成知识。审核经验只允许专家登记，仍需有效文档支持技术结论。')
        memory_source = st.text_input('关联文档版本 ID（选填）', max_chars=64,
            help='可从文档版本目录复制；关联版本失效后，该记忆不会注入新问答。')
        memory_expiry = st.date_input('记忆失效日期', value=date.today() + timedelta(days=365)) if st.checkbox('设置记忆有效期') else None
        if st.button('保存长期记忆'):
            if not memory_content.strip():
                st.warning('请先填写记忆内容。')
            else:
                response = post_backend(f'{BACKEND_URL}/memories', json={'user_ctx': st.session_state.user_ctx,
                    'kind': {'回答偏好': 'preference', '用户背景': 'background', '审核经验': 'reviewed_experience'}[memory_kind],
                    'content': memory_content, 'source_id': memory_source.strip(),
                    'valid_until': memory_expiry.isoformat() if memory_expiry else None}, timeout=15)
                if response.ok:
                    st.success('记忆已保存，仅用于当前用户和范围。')
                else:
                    st.error('保存失败，请检查权限和记忆数量。')
        if st.button('查看已保存记忆'):
            response = post_backend(f'{BACKEND_URL}/memories/list',
                json={'user_ctx': st.session_state.user_ctx}, timeout=15)
            if response.ok:
                st.json(response.json()['memories'])
        delete_id = st.text_input('需要删除的记忆 ID')
        if st.button('删除指定记忆') and delete_id:
            response = post_backend(f'{BACKEND_URL}/memories/{delete_id}/delete',
                json={'user_ctx': st.session_state.user_ctx}, timeout=15)
            st.success('记忆已删除') if response.ok else st.error('删除失败，请检查记忆 ID。')
    for message in st.session_state.chat_history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant":
                render_sources(message)
    question = st.chat_input("例如：F0012 故障应该如何排查？")
    if question and question.strip():
        st.session_state.chat_history.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.spinner("正在处理你的问题..."):
                try:
                    response = requests.post(f"{BACKEND_URL}/query",
                        json={"question": question.strip(), "user_ctx": st.session_state.user_ctx,
                              "top_k": top_k, 'session_id': st.session_state.conversation_id,
                              'as_of': as_of, 'comparison_dates': comparison_dates}, timeout=600)
                    response.raise_for_status()
                    result = response.json()
                    message = {"role": "assistant", "content": result["answer"],
                               "sources": result.get("sources", []),
                               "diagnostics": result.get("diagnostics", {}),
                               'confidence': result.get('confidence', {}),
                               'retrieval_steps': result.get('retrieval_steps', [])}
                    st.session_state.chat_history.append(message)
                    st.markdown(message["content"])
                    render_sources(message)
                except requests.RequestException:
                    st.error("知识服务暂不可用，请检查后端状态和模型配置后重试。")


def login_page():
    st.title("智能工厂知识助手登录")
    st.write("请使用您的工厂账户登录")

    with st.form("login_form"):
        col1, col2 = st.columns(2)
        with col1:
            tenant_id = st.selectbox("租户", ["tenant_A", "tenant_B", "tenant_C"], index=0)
            plant_id = st.selectbox("工厂", ["plant_A1", "plant_A2", "plant_B1", "plant_C1"], index=0)
        with col2:
            role = st.selectbox("角色", ["操作员", "工程师", "专家"], index=1)
            # 确保line_id使用小写
            line_id = st.selectbox("产线", ["line_a1", "line_a2", "line_b1", "line_c1"], index=0)

        username = st.text_input("用户名", value="engineer_li")
        password = st.text_input("密码", type="password", value="securepass123")

        submitted = st.form_submit_button("登录")
        if submitted:
            if username and password:
                st.session_state.user_ctx = {
                    "tenant_id": tenant_id,
                    "plant_id": plant_id,
                    "role": role,
                    "line_id": line_id,
                    "user_id": username.strip()
                }
                st.session_state.chat_history = []
                st.session_state.conversation_id = str(uuid4())
                st.success("登录成功！正在跳转主界面...")
                time.sleep(1)
                st.rerun()
            else:
                st.error("用户名和密码不能为空")





# 设备参数页面
# app_frontend.py
def equipment_parameters():
    from business_ui import render_business
    render_business('equipment', BACKEND_URL, post_backend)


def fault_library():
    from business_ui import render_business
    render_business('faults', BACKEND_URL, post_backend)


# 工艺文档页面
def process_documents():
    st.header("工艺文档库")
    st.info("浏览当前范围内有效的已发布知识文档；原文可在知识助手的引用中查看。")
    # 添加搜索框
    search_query = st.text_input("搜索文档标题", "")
    # 文档分类选项
    categories = ["全部", "标准", "图纸", "配方"]
    selected_category = st.selectbox("文档分类", categories)

    # 文档列表
    with st.spinner("加载文档中..."):
        try:
            response = requests.post(
                f"{BACKEND_URL}/documents/list",
                json={"user_ctx": st.session_state.user_ctx}, timeout=15
            )

            if response.status_code == 200:
                catalog = response.json()
                documents = [doc for doc in catalog['documents'] if doc['id'] in catalog['current_ids']
                    and (selected_category == '全部' or doc.get('category') == selected_category)
                    and search_query.lower() in doc['title'].lower()]

                if documents:
                    st.success(f"找到 {len(documents)} 份文档")
                    # 显示文档列表
                    for doc in documents:
                        with st.expander(f"{doc['title']} · {doc['version']}"):
                            st.markdown(f"**文档ID**: {doc['id']}")
                            st.markdown(f"**访问级别**: {doc['access_level']}")
                            st.caption(f"系列：{doc['family_name']} · 生效：{doc['effective_from']} · 文件：{doc['filename']}")
                else:
                    st.info("当前产线没有可用的工艺文档")
            else:
                st.error(f"查询失败: {response.text}")
        except Exception as e:
            st.error(f"发生错误: {str(e)}")


# 系统设置页面
def system_settings():
    from settings_ui import render_settings
    with st.container(key='settings_workspace'):
        render_settings(BACKEND_URL, post_backend)


def sync_navigation(key):
    st.session_state.selected_tab = st.session_state[key]


def main_page():
    # 顶部导航栏
    with st.container():
        st.session_state.selected_tab = option_menu(
            menu_title=None,
            options=["知识助手", "设备参数", "故障库", "工艺文档", "系统设置"],
            icons=["chat", "cpu", "bug", "file-earmark-text", "gear"],
            orientation="horizontal",
            default_index=["知识助手", "设备参数", "故障库", "工艺文档", "系统设置"].index(st.session_state.selected_tab),
            key="top_nav",
            on_change=sync_navigation
        )

    # 租户信息显示
    st.sidebar.subheader(f"当前用户")
    st.sidebar.markdown(f"**用户**: {st.session_state.user_ctx.get('user_id', 'demo')}")
    st.sidebar.markdown(f"**租户**: {st.session_state.user_ctx['tenant_id']}")
    st.sidebar.markdown(f"**工厂**: {st.session_state.user_ctx['plant_id']}")
    st.sidebar.markdown(f"**产线**: {st.session_state.user_ctx['line_id']}")
    st.sidebar.markdown(f"**角色**: {st.session_state.user_ctx['role']}")

    st.sidebar.divider()
    st.sidebar.markdown("### 快捷操作")
    if st.sidebar.button("清除聊天记录"):
        response = post_backend(f"{BACKEND_URL}/sessions/{st.session_state.conversation_id}/clear",
            json={'user_ctx': st.session_state.user_ctx}, timeout=15)
        if response.ok or response.status_code == 404:
            st.session_state.chat_history = []
            st.session_state.conversation_id = str(uuid4())
            st.rerun()
        else:
            st.sidebar.error('清除失败，请等待当前回答完成后重试。')

    # 功能区域
    if st.session_state.selected_tab == "知识助手":
        knowledge_assistant()
    elif st.session_state.selected_tab == "设备参数":
        equipment_parameters()
    elif st.session_state.selected_tab == "故障库":
        fault_library()
    elif st.session_state.selected_tab == "工艺文档":
        process_documents()
    elif st.session_state.selected_tab == "系统设置":
        system_settings()

# 应用路由
if 'user_ctx' in st.session_state and st.session_state.user_ctx:
    main_page()
else:
    login_page()
