
# app_frontend.py
import streamlit as st
import requests
import json
import time
from streamlit_option_menu import option_menu

# 页面设置
st.set_page_config(
    page_title="智能工厂知识助手",
    page_icon="🏭",
    layout="wide"
)

# 后端API配置
BACKEND_URL = "http://127.0.0.1:8000"

# 初始化会话状态
if 'user_ctx' not in st.session_state:
    st.session_state.user_ctx = None
if 'chat_history' not in st.session_state:
    st.session_state.chat_history = []
if 'selected_tab' not in st.session_state:
    st.session_state.selected_tab = "知识助手"


def knowledge_assistant():
    st.header("🏭 智能工厂知识助手")

    # 显示用户上下文
    with st.expander("当前用户上下文"):
        st.markdown(f"**租户**: {st.session_state.user_ctx['tenant_id']}")
        st.markdown(f"**工厂**: {st.session_state.user_ctx['plant_id']}")
        st.markdown(f"**产线**: {st.session_state.user_ctx['line_id']}")
        st.markdown(f"**角色**: {st.session_state.user_ctx['role']}")
        try:
            # 直接查询租户表
            response = requests.post(
                f"{BACKEND_URL}/execute-sql",
                json={
                    "sql": f"SELECT industry FROM tenants WHERE tenant_id = '{st.session_state.user_ctx['tenant_id']}'",
                    "user_ctx": st.session_state.user_ctx
                }
            )
            industry = response.json()[0]['industry'] if response.json() else "未知"
            st.markdown(f"**行业**: {industry}")
        except:
            st.markdown("**行业**: 查询失败")
    # 聊天历史显示
    chat_container = st.container(height=500)
    with chat_container:
        for idx, msg in enumerate(st.session_state.chat_history):
            if msg["role"] == "user":
                with st.chat_message("user", avatar="🧑‍🔧"):
                    st.markdown(msg["content"])
            else:
                with st.chat_message("assistant", avatar="🤖"):
                    # 添加来源标识
                    source_type = "技术文档" if any(
                        s.get('tool') == 'chroma_search' for s in msg.get("source_data", [])) else "结构化数据"
                    st.caption(f"来源: {source_type}")
                    st.markdown(msg["content"])
                    # 更健壮的空值检查
                    if msg.get("source_data"):
                        with st.expander("查看来源数据"):
                            for source in msg["source_data"]:
                                # 确保 source 是字典类型
                                if not isinstance(source, dict):
                                    st.error(f"无效的来源数据格式: {type(source)}")
                                    continue

                                # 安全获取工具名称
                                tool_name = source.get('tool', '未知工具')
                                st.markdown(f"**工具**: {tool_name}")

                                # 安全显示查询语句
                                query = source.get('query')
                                if query:
                                    st.code(query, language="sql")
                                else:
                                    st.warning("无查询语句")

                                # 安全显示结果
                                result = source.get('result')
                                if result:
                                    try:
                                        # 尝试解析为JSON
                                        result_data = json.loads(result)
                                        st.json(result_data)
                                    except:
                                        # 如果不是JSON，直接显示文本
                                        st.text(result)
                                else:
                                    st.warning("无结果数据")

    # 用户输入
    user_input = st.chat_input("请输入您的问题...")

    if user_input:
        cleaned_input = user_input.strip()
        cleaned_input = cleaned_input.replace("'", "")
        # 添加用户消息到历史
        st.session_state.chat_history.append({"role": "user", "content": user_input})

        # 显示用户消息
        with chat_container:
            with st.chat_message("user", avatar="🧑‍🔧"):
                st.markdown(user_input)

        # 调用后端API
        with st.spinner("正在思考..."):
            try:
                response = requests.post(
                    f"{BACKEND_URL}/query",
                    json={
                        "question": cleaned_input,
                        "user_ctx": st.session_state.user_ctx
                    }
                )

                if response.status_code == 200:
                    result = response.json()

                    # 添加助手回复到历史
                    st.session_state.chat_history.append({
                        "role": "assistant",
                        "content": result["answer"],
                        "source_data": result.get("source_data", [])  # 修改为 source_data
                    })

                    # 显示助手回复
                    with chat_container:
                        with st.chat_message("assistant", avatar="🤖"):
                            st.markdown(result["answer"])
                            if "source_data" in result:
                                with st.expander("查看来源数据"):
                                    for source in result["source_data"]:
                                        st.markdown(f"**工具**: {source['tool']}")
                                        st.code(source['query'], language="sql")
                                        st.json(source['result'])
                else:
                    # st.error(f"请求失败: {response.text}")
                    # 更健壮的错误处理
                    try:
                        error_detail = response.json().get('detail', response.text)
                    except:
                        error_detail = response.text[:500]  # 限制长度防止显示问题

                    # 更友好的错误提示
                    error_msg = f"请求失败: {error_detail}"
                    st.error(error_msg)

                    # 添加到聊天历史
                    st.session_state.chat_history.append({
                        "role": "assistant",
                        "content": f"抱歉，处理您的请求时出错: {error_msg}"
                    })
            except Exception as e:
                st.error(f"发生错误: {str(e)}")
                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": f"抱歉，处理您的请求时出错: {str(e)}"
                })





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
                    "line_id": line_id
                }
                st.session_state.chat_history = []
                st.success("登录成功！正在跳转主界面...")
                time.sleep(1)
                st.rerun()
            else:
                st.error("用户名和密码不能为空")





# 设备参数页面
# app_frontend.py
def equipment_parameters():
    st.header("设备参数管理")
    st.info("查询当前产线的设备信息")

    # 显示当前产线
    st.markdown(f"**当前产线**: {st.session_state.user_ctx['line_id']}")

    # 设备查询表单
    with st.form("equipment_form"):
        col1, col2 = st.columns([3, 1])
        with col1:
            device_name = st.text_input("设备名称", placeholder="输入设备名称或留空查询所有设备")
        with col2:
            st.text("")
            st.text("")
            submit = st.form_submit_button("查询")

    if submit:
        with st.spinner("查询中..."):
            try:
                # 正确的请求格式
                request_data = {
                    "device_name": device_name,
                    "user_ctx": st.session_state.user_ctx
                }

                # 显示发送的请求
                st.json(request_data)
                # 调用后端API
                response = requests.post(
                    f"{BACKEND_URL}/query-equipment",
                    json={
                        "device_name": device_name,
                        "user_ctx": st.session_state.user_ctx
                    }
                )

                if response.status_code == 200:
                    results = response.json()

                    if results:
                        if device_name:
                            # 显示设备详细信息
                            st.subheader(f"设备: {device_name} 的参数")
                            st.dataframe(results)
                        else:
                            # 显示所有设备列表
                            st.subheader("当前产线所有设备")
                            devices = [d['device'] for d in results]
                            st.write(", ".join(devices))
                    else:
                        st.warning("未找到匹配的设备信息")
                else:
                    st.error(f"查询失败: {response.text}")
            except Exception as e:
                st.error(f"发生错误: {str(e)}")


# 故障库页面
def fault_library():
    st.header("故障知识库")
    st.info("查询设备故障信息及解决方案")

    # 查询故障
    with st.form("fault_query_form"):
        col1, col2 = st.columns(2)
        with col1:
            device_type = st.text_input("设备类型", placeholder="如: 激光焊接机")
        with col2:
            fault_code = st.text_input("故障代码", placeholder="如: F0012")

        submit = st.form_submit_button("查询故障解决方案")

    if submit:
        with st.spinner("查询中..."):
            try:
                # 构造SQL查询
                sql = f"""
                SELECT device_type, code, description, solution, severity 
                FROM fault_knowledge 
                WHERE tenant_id = '{st.session_state.user_ctx['tenant_id']}'
                """

                if device_type:
                    sql += f" AND device_type LIKE '%{device_type}%'"
                if fault_code:
                    sql += f" AND code LIKE '%{fault_code}%'"

                # 调用后端执行SQL
                response = requests.post(
                    f"{BACKEND_URL}/execute-sql",
                    json={
                        "sql": sql,
                        "user_ctx": st.session_state.user_ctx
                    }
                )

                if response.status_code == 200:
                    results = response.json()

                    # 显示结果
                    if results:
                        st.success(f"找到 {len(results)} 条故障记录")
                        for fault in results:
                            with st.expander(f"{fault['device_type']} - {fault['code']}: {fault['description']}"):
                                st.markdown(f"**严重程度**: {fault['severity']}")
                                st.markdown("**解决方案**:")
                                st.markdown(fault['solution'])
                    else:
                        st.info("未找到匹配的故障信息")
                else:
                    st.error(f"查询失败: {response.text}")
            except Exception as e:
                st.error(f"发生错误: {str(e)}")


# 工艺文档页面
def process_documents():
    st.header("工艺文档库")
    st.info("浏览和下载工艺文档")
    # 添加搜索框
    search_query = st.text_input("搜索文档标题", "")
    # 文档分类选项
    categories = ["全部", "标准", "图纸", "配方"]
    selected_category = st.selectbox("文档分类", categories)

    # 文档列表
    with st.spinner("加载文档中..."):
        try:
            # 构造SQL查询
            sql = f"""
            SELECT doc_id, title, category, access_level 
            FROM process_documents 
            WHERE line_id = '{st.session_state.user_ctx['line_id']}'
            """

            if selected_category != "全部":
                sql += f" AND category = '{selected_category}'"
            if search_query:
                sql += f" AND title LIKE '%{search_query}%'"
            # 调用后端执行SQL
            response = requests.post(
                f"{BACKEND_URL}/execute-sql",
                json={
                    "sql": sql,
                    "user_ctx": st.session_state.user_ctx
                }
            )

            if response.status_code == 200:
                documents = response.json()

                if documents:
                    st.success(f"找到 {len(documents)} 份文档")
                    # 显示文档列表
                    for doc in documents:
                        # 检查用户权限
                        role_level = {"操作员": 1, "工程师": 2, "专家": 3}
                        doc_level = {"操作员": 1, "工程师": 2, "专家": 3}

                        if doc_level[doc["access_level"]] <= role_level[st.session_state.user_ctx["role"]]:
                            with st.expander(f"{doc['title']} ({doc['category']})"):
                                st.markdown(f"**文档ID**: {doc['doc_id']}")
                                st.markdown(f"**访问级别**: {doc['access_level']}")

                                # 模拟文档预览
                                st.markdown("**文档预览**:")
                                st.markdown("> 这是一份关于生产工艺的标准文档，包含详细的操作步骤和质量要求...")

                                # 下载按钮
                                st.download_button(
                                    label="下载文档",
                                    data="这是文档内容...",
                                    file_name=f"{doc['doc_id']}.pdf",
                                    mime="application/pdf"
                                )
                        else:
                            st.warning(f"文档 '{doc['title']}' 需要 {doc['access_level']} 权限访问")
                else:
                    st.info("当前产线没有可用的工艺文档")
            else:
                st.error(f"查询失败: {response.text}")
        except Exception as e:
            st.error(f"发生错误: {str(e)}")


# 系统设置页面
def system_settings():
    st.header("系统设置")

    tab1, tab2,tab3 = st.tabs(["用户管理", "系统状态", "知识库管理"])

    with tab1:
        if st.session_state.user_ctx["role"] == "专家":
            st.subheader("用户管理")
            st.write("专家用户可以管理系统用户")

            # 添加用户表单
            with st.form("add_user_form"):
                st.write("### 添加新用户")
                new_username = st.text_input("用户名")
                new_password = st.text_input("密码", type="password")
                new_role = st.selectbox("角色", ["操作员", "工程师"])
                new_tenant = st.selectbox("租户", ["tenant_A", "tenant_B"])
                new_plant = st.selectbox("工厂", ["plant_A1", "plant_A2", "plant_B1"])
                new_line = st.selectbox("产线", ["line_A1", "line_A2", "line_B1"])

                if st.form_submit_button("添加用户"):
                    st.success(f"用户 {new_username} 添加成功！")
        else:
            st.warning("只有专家用户可以访问此功能")

    with tab2:
        st.subheader("系统状态")
        st.info("检查系统各组件运行状态")

        # 后端服务状态
        try:
            response = requests.get(f"{BACKEND_URL}/health")
            if response.status_code == 200:
                st.success("后端服务: 运行正常")
            else:
                st.error(f"后端服务: 异常 ({response.status_code})")
        except:
            st.error("后端服务: 不可达")

        # 数据库状态
        try:
            response = requests.get(f"{BACKEND_URL}/db-status")
            if response.status_code == 200:
                status = response.json()
                st.success(f"MySQL数据库: 连接正常 (租户数: {status['tenants']}, 设备参数: {status['parameters']})")
            else:
                st.error(f" MySQL数据库: 连接异常 ({response.status_code})")
        except:
            st.error("MySQL数据库: 不可达")

        # 向量数据库状态
        try:
            response = requests.get(f"{BACKEND_URL}/vector-status")
            if response.status_code == 200:
                status = response.json()
                st.success(f" 向量数据库: {status['status']} (文档数: {status['documents']})")
            else:
                st.error(f" 向量数据库: 连接异常 ({response.status_code})")
        except:
            st.error("❌ 向量数据库: 不可达")
    with tab3:
        st.subheader("知识库文档上传")
        st.info("上传技术文档到向量知识库（支持PDF、TXT、DOCX等格式）")

        if st.session_state.user_ctx["role"] != "专家":
            st.warning("只有专家用户可以上传文档")
            return

        uploaded_file = st.file_uploader(
            "选择技术文档",
            type=["pdf", "txt", "docx"],
            accept_multiple_files=False
        )

        doc_title = st.text_input("文档标题", "")
        doc_category = st.selectbox("文档分类", ["标准", "图纸", "配方", "故障手册", "操作指南"])
        access_level = st.selectbox("访问权限", ["操作员", "工程师", "专家"])

        if st.button("上传到知识库") and uploaded_file:
            with st.spinner("处理文档中..."):
                try:
                    # 准备上传数据
                    files = {"file": (uploaded_file.name, uploaded_file.getvalue())}
                    metadata = {
                        "title": doc_title,
                        "category": doc_category,
                        "access_level": access_level,
                        "user_ctx": json.dumps(st.session_state.user_ctx)
                    }

                    # 调用后端API
                    response = requests.post(
                        f"{BACKEND_URL}/upload-document",
                        files=files,
                        data=metadata
                    )

                    if response.status_code == 200:
                        st.success("文档上传成功！")
                        st.json(response.json())
                    else:
                        # 更详细的错误信息
                        try:
                            error_detail = response.json().get('detail', response.text)
                        except:
                            error_detail = response.text[:500]

                        st.error(f"上传失败: {error_detail}")
                except Exception as e:
                    st.error(f"上传错误: {str(e)}")
def main_page():
    # 顶部导航栏
    with st.container():
        st.session_state.selected_tab = option_menu(
            menu_title=None,
            options=["知识助手", "设备参数", "故障库", "工艺文档", "系统设置"],
            icons=["chat", "cpu", "bug", "file-earmark-text", "gear"],
            orientation="horizontal",
            default_index=0,
            key="top_nav"
        )

    # 租户信息显示
    st.sidebar.subheader(f"当前用户")
    st.sidebar.markdown(f"**租户**: {st.session_state.user_ctx['tenant_id']}")
    st.sidebar.markdown(f"**工厂**: {st.session_state.user_ctx['plant_id']}")
    st.sidebar.markdown(f"**产线**: {st.session_state.user_ctx['line_id']}")
    st.sidebar.markdown(f"**角色**: {st.session_state.user_ctx['role']}")

    st.sidebar.divider()
    st.sidebar.markdown("### 快捷操作")
    if st.sidebar.button("清除聊天记录"):
        st.session_state.chat_history = []
        st.rerun()

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