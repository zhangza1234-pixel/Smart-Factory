"""Task-focused settings pages; render only the selected section."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import json

import requests
import streamlit as st


STATUS = {'draft': '草稿', 'published': '已发布', 'withdrawn': '已撤回'}


def error_message(response):
    try:
        detail = response.json().get('detail', '操作失败，请稍后重试。')
        return detail if isinstance(detail, str) else '填写内容不符合要求，请检查日期、版本号和必填项。'
    except (ValueError, AttributeError):
        return '服务暂不可用，请检查后端状态。'


def render_settings(backend_url, post):
    ctx = st.session_state.user_ctx
    st.markdown('''<style>
        .st-key-settings_workspace [data-testid="stVerticalBlock"] {gap: 0.65rem;}
        .st-key-settings_workspace hr {margin: 0.4rem 0 0.8rem;}
    </style>''', unsafe_allow_html=True)
    st.header('系统设置')
    st.caption(f"管理当前范围的知识资料与运行状态 · {ctx['tenant_id']} / {ctx['plant_id']} / {ctx['line_id']}")
    section = st.radio('设置导航', ['文档上传', '版本管理', '运行状态', '账户与权限'],
                       horizontal=True, key='settings_section', label_visibility='collapsed')
    st.divider()
    if section == '文档上传':
        upload_document(backend_url, post, ctx)
    elif section == '版本管理':
        manage_versions(backend_url, post, ctx)
    elif section == '运行状态':
        runtime_status(backend_url)
    else:
        account_info(ctx, backend_url, post)


def upload_document(url, post, ctx):
    st.subheader('上传知识文档')
    st.caption('选择文件 → 填写基本信息 → 预览或上传。版本与切分设置可按需展开。')
    if ctx['role'] != '专家':
        st.info('文档上传由专家维护。你可以在“版本管理”中查看当前权限内的文档。')
        return
    with st.container(border=True):
        st.markdown('**基本信息**')
        file = st.file_uploader('选择技术文档', type=['pdf', 'txt', 'md', 'docx'],
                               help='支持 PDF、TXT、Markdown、Word；文件大小仍受后端上传限制。', key='settings_upload_file')
        title = st.text_input('文档标题', max_chars=200, placeholder='选填，留空时自动使用文件名', key='settings_upload_title')
        left, right = st.columns(2)
        category = left.selectbox('文档分类', ['标准', '图纸', '配方', '故障手册', '操作指南'], key='settings_upload_category')
        access = right.selectbox('访问权限', ['操作员', '工程师', '专家'], key='settings_upload_access',
                                 help='选择可以阅读此文档的最低角色。')
    with st.expander('版本与生效时间'):
        st.caption('同一标准的不同年份版本，请使用相同的文档系列名称。')
        family = st.text_input('文档系列', max_chars=200, key='settings_upload_family',
                               placeholder='例如：焊接检验规范；留空时使用文档标题')
        left, right = st.columns(2)
        version = left.text_input('版本号', value=date.today().isoformat(), max_chars=80, key='settings_upload_version')
        start = right.date_input('生效日期', value=date.today(), key='settings_upload_start')
        expires = st.checkbox('设置失效日期', key='settings_upload_expires')
        end = st.date_input('失效日期（当天起不适用）', key='settings_upload_end') if expires else None
        publish = st.checkbox('完整入库后发布此版本', value=True, key='settings_upload_publish',
                              help='取消勾选后保存为草稿，可在版本管理中发布。未来生效的版本不会提前替代当前版本。')
    with st.expander('文档切分设置'):
        strategy_label = st.selectbox('文档切分方式', ['按标题和段落，超长内容递归切分', '纯递归切分'], key='settings_upload_split')
        st.caption('推荐按标题和段落保留章节结构；超长内容会继续递归切分。预览不会写入知识库。')
    strategy = 'structured' if strategy_label.startswith('按标题') else 'recursive'
    st.caption(f"版本 {version or '未填写'} · 生效日期 {start.isoformat()} · {'上传后发布' if publish else '保存为草稿'} · {'按标题和段落切分' if strategy == 'structured' else '递归切分'}")
    if file is None:
        st.caption('请先选择文档，再预览或上传。')
    left, right, _ = st.columns([1, 1, 2])
    preview = left.button('预览切分结果', disabled=file is None, use_container_width=True)
    upload = right.button('上传到知识库', type='primary', disabled=file is None, use_container_width=True)
    if preview:
        with st.spinner('正在预览文档切分…'):
            response = post(f'{url}/test_file-upload', files={'file': (file.name, file.getvalue())},
                            data={'split_strategy': strategy}, timeout=120)
        if response.ok:
            result = response.json()
            st.subheader('切分预览')
            st.caption(f"共 {result['chunk_count']} 个片段，展示前 10 个。尚未入库。")
            for index, chunk in enumerate(result['preview'], 1):
                with st.expander(f"片段 {index} · {chunk['metadata'].get('section_path') or '正文'}"):
                    st.text(chunk['text'])
        else:
            st.error('预览失败：' + error_message(response))
    if upload:
        if not version.strip():
            st.error('请展开“版本与生效时间”，填写版本号。')
            return
        if end and end <= start:
            st.error('失效日期必须晚于生效日期，请检查“版本与生效时间”。')
            return
        metadata = {'title': title, 'category': category, 'access_level': access,
            'split_strategy': strategy, 'family_name': family, 'version': version.strip(),
            'effective_from': start.isoformat(), 'publish': str(publish).lower(), 'user_ctx': json.dumps(ctx)}
        if end:
            metadata['effective_to'] = end.isoformat()
        with st.spinner('正在处理文档并写入知识库…'):
            response = post(f'{url}/upload-document', files={'file': (file.name, file.getvalue())},
                            data=metadata, timeout=600)
        if response.ok:
            result = response.json()
            st.success(f"文档上传成功！标题：{result.get('title', file.name)}")
            st.caption(f"版本：{result.get('version')} · 状态：{STATUS.get(result.get('version_status'), result.get('version_status'))} · 生效：{result.get('effective_from')}")
            st.info('可前往“版本管理”查看该文档及发布状态。')
        else:
            st.error('上传失败：' + error_message(response))


def version_label(doc, current):
    if doc['status'] != 'published':
        return STATUS.get(doc['status'], doc['status'])
    if doc['id'] in current:
        return '当前有效'
    if doc['effective_from'] > date.today().isoformat():
        return '未来生效'
    if doc.get('effective_to') and doc['effective_to'] <= date.today().isoformat():
        return '已失效'
    return '历史版本'


def manage_versions(url, post, ctx):
    st.subheader('文档版本管理')
    st.caption('按名称选择文档即可维护。旧版保留供历史查询，撤回当前版本后不会自动回退旧版。')
    if st.session_state.pop('settings_version_saved', False):
        st.success('版本状态已更新。')
    st.button('刷新版本目录')
    response = post(f'{url}/documents/list', json={'user_ctx': ctx}, timeout=15)
    if not response.ok:
        st.error('无法读取版本目录：' + error_message(response))
        return
    catalog = response.json()
    docs, current = catalog['documents'], set(catalog['current_ids'])
    columns = st.columns(3)
    columns[0].metric('可见文档版本', len(docs))
    columns[1].metric('当前有效', len(current))
    columns[2].metric('待发布草稿', sum(doc['status'] == 'draft' for doc in docs))
    left, right = st.columns([2, 1])
    keyword = left.text_input('搜索标题或系列', key='settings_versions_keyword')
    status = right.selectbox('筛选版本状态', ['全部', '当前有效', '草稿', '历史版本', '未来生效', '已撤回', '已失效'], key='settings_versions_status')
    filtered = sorted([doc for doc in docs if
        keyword.strip().lower() in (doc['title'] + ' ' + doc['family_name']).lower()
        and (status == '全部' or version_label(doc, current) == status)],
        key=lambda doc: (doc['family_name'], doc['effective_from'], doc['version']), reverse=True)
    if not filtered:
        st.info('没有匹配版本，请更换筛选条件，或前往“文档上传”添加文档。')
        return
    st.dataframe([{'文档标题': doc['title'], '文档系列': doc['family_name'], '版本': doc['version'],
        '状态': version_label(doc, current), '生效日期': doc['effective_from'],
        '失效日期': doc.get('effective_to') or '未设置', '访问权限': doc['access_level']} for doc in filtered],
        use_container_width=True, hide_index=True)
    st.caption(f'共找到 {len(filtered)} 个版本。')
    by_id = {doc['id']: doc for doc in filtered}
    with st.container(border=True):
        selected = st.selectbox('选择文档版本', list(by_id), key='settings_version_selected',
            format_func=lambda uid: f"{by_id[uid]['family_name']} / {by_id[uid]['version']} / {version_label(by_id[uid], current)}")
        doc = by_id[selected]
        st.caption(f"文件：{doc['filename']} · 片段数：{doc['chunk_count']} · 状态：{version_label(doc, current)}")
        if ctx['role'] != '专家':
            st.info('当前角色可查看版本；发布和撤回由专家维护。')
            return
        published = doc['status'] == 'published'
        st.caption('撤回后该版本将不再参与查询，历史查询也将排除此版本。' if published else
                   '发布前会检查片段是否完整；未来生效的版本仍需等到生效日期才参与查询。')
        if st.button('撤回所选版本' if published else '发布所选版本', type='primary'):
            response = post(f'{url}/documents/{selected}/status', json={'user_ctx': ctx,
                'status': 'withdrawn' if published else 'published'}, timeout=30)
            if response.ok:
                st.session_state['settings_version_saved'] = True
                st.rerun()
            else:
                st.error(error_message(response))


def runtime_status(url):
    st.subheader('运行状态')
    if st.session_state.user_ctx['role'] != '专家':
        st.info('运行状态由专家查看。')
        return
    st.caption('查看服务连接情况。刷新检查只在此页面执行，不影响文档上传。')
    refresh = st.button('刷新运行状态', type='primary')
    if refresh or 'settings_runtime_status' not in st.session_state:
        from api_client import auth_headers
        headers = auth_headers()
        def check(path):
            try:
                response = requests.get(url + path, headers=headers, timeout=10)
                return response.json() if response.ok else None
            except (requests.RequestException, ValueError):
                return None
        with st.spinner('正在检查服务…'):
            paths = ['/health', '/db-status', '/vector-status', '/memory-status']
            with ThreadPoolExecutor(max_workers=4) as pool:
                values = list(pool.map(check, paths))
            st.session_state.settings_runtime_status = dict(zip(paths, values))
            st.session_state.settings_runtime_checked = datetime.now().strftime('%H:%M:%S')
    checks = st.session_state.settings_runtime_status
    st.caption('最近检查：' + st.session_state.settings_runtime_checked + '；连接状态以该次检查为准。')
    components = [('/health', '后端服务'), ('/db-status', '业务数据库'),
                  ('/vector-status', '文档检索库'), ('/memory-status', '会话与缓存')]
    for index, (path, name) in enumerate(components):
        if index % 2 == 0:
            columns = st.columns(2)
        data = checks[path]
        with columns[index % 2].container(border=True):
            st.markdown(f'**{name}**')
            if not data:
                st.error('连接异常')
                st.caption('请检查后端服务、数据库连接和账户权限。')
            elif path == '/memory-status':
                if data.get('redis') == 'connected':
                    st.success('MySQL 与 Redis 正常')
                else:
                    st.warning('MySQL 正常 · Redis 未连接')
                    st.caption('会话仍由 MySQL 保存和恢复，当前使用数据库兜底。')
            else:
                st.success('运行正常')
                if path == '/db-status':
                    st.caption(f"租户 {data['tenants']} 个 · 设备参数 {data['parameters']} 条")
                elif path == '/vector-status':
                    st.caption(f"已入库片段 {data['documents']} 个")


def account_info(ctx, url, post):
    st.subheader('账户与权限')
    with st.container(border=True):
        st.markdown('**当前账户**')
        left, right = st.columns(2)
        left.text(f"用户名：{st.session_state.get('username', '')}\n当前角色：{ctx['role']}")
        right.text(f"租户：{ctx['tenant_id']}\n工厂：{ctx['plant_id']}\n产线：{ctx['line_id']}")
    st.markdown('**角色权限说明**')
    st.dataframe([
        {'角色': '操作员', '业务记录': '查看', '知识文档': '查看权限内资料', '上传 / 发布 / 撤回': '不可操作'},
        {'角色': '工程师', '业务记录': '查看、新增、修改', '知识文档': '查看权限内资料', '上传 / 发布 / 撤回': '不可操作'},
        {'角色': '专家', '业务记录': '查看、新增、修改', '知识文档': '查看权限内资料', '上传 / 发布 / 撤回': '可以操作'},
    ], hide_index=True, use_container_width=True)
    st.caption('账号身份与访问范围由服务端确定。需要分配或调整账号时，请联系本租户专家。')
    if ctx['role'] != '专家':
        return
    if notice := st.session_state.pop('account_notice', None):
        st.success(notice)
    from api_client import get_backend
    response = get_backend(url + '/auth/accounts', timeout=15)
    scopes_response = get_backend(url + '/auth/scopes', timeout=15)
    if not response.ok or not scopes_response.ok:
        st.error(error_message(response if not response.ok else scopes_response))
        return
    accounts = response.json()['accounts']
    scopes = scopes_response.json()['scopes']
    st.subheader('本租户账号')
    st.dataframe([{'用户名': row['username'], '角色': row['role'], '工厂': row['plant_id'],
                   '产线': row['line_id'], '状态': '启用' if row['active'] else '禁用'} for row in accounts],
                 hide_index=True, use_container_width=True)
    if scopes:
        with st.form('create_account', clear_on_submit=True):
            st.markdown('**分配账号**')
            username = st.text_input('新账号用户名', help='使用英文字母、数字、下划线、点或短横线')
            password = st.text_input('初始密码（至少12个字符）', type='password')
            role = st.selectbox('账号角色', ['操作员', '工程师', '专家'])
            scope_index = st.selectbox('账号所属工厂与产线', range(len(scopes)),
                format_func=lambda i: f"{scopes[i]['plant_name']} / {scopes[i]['line_name']}")
            submitted = st.form_submit_button('创建账号')
        if submitted:
            scope = scopes[scope_index]
            result = post(url + '/auth/accounts', json={'username': username.strip(), 'password': password,
                'role': role, 'plant_id': scope['plant_id'], 'line_id': scope['line_id']}, timeout=15)
            if result.ok:
                st.session_state.account_notice = '账号已创建，请将账号和初始密码交给对应人员。'
                st.rerun()
            else:
                st.error(error_message(result))
    others = {row['user_id']: row for row in accounts if row['user_id'] != ctx['user_id']}
    if others:
        with st.form('update_account', clear_on_submit=True):
            st.markdown('**管理账号**')
            user_id = st.selectbox('选择账号', list(others), format_func=lambda uid: others[uid]['username'])
            action = st.selectbox('操作', ['禁用账号', '启用账号', '重置密码'])
            password = st.text_input('重置后的密码（仅重置密码时填写）', type='password')
            submitted = st.form_submit_button('执行账号操作')
        if submitted:
            changes = {'password': password} if action == '重置密码' else {'active': action == '启用账号'}
            result = post(url + '/auth/accounts/' + user_id, json=changes, timeout=15)
            if result.ok:
                st.session_state.account_notice = '账号已更新，该账号原有登录已失效。'
                st.rerun()
            else:
                st.error(error_message(result))
