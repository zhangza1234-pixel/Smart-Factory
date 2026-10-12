"""Chinese business forms for staff who do not use SQL."""
import streamlit as st


LABELS = {'device_name': '设备名称', 'parameter': '参数名称', 'value': '参数值（含单位）',
          'tolerance': '允许偏差 / 适用说明', 'last_updated': '更新时间',
          'device_type': '设备类型', 'code': '故障代码', 'description': '故障现象',
          'solution': '处理步骤与注意事项', 'severity': '严重程度'}


def render_business(kind, backend_url, post):
    equipment = kind == 'equipment'
    ctx = st.session_state.user_ctx
    prefix = f"{kind}_{ctx['tenant_id']}_{ctx['line_id']}"
    st.header('设备参数管理' if equipment else '故障知识库')
    st.caption(f"当前工厂：{ctx['plant_id']} · 当前产线：{ctx['line_id']}" if equipment
               else f"当前租户：{ctx['tenant_id']} · 故障经验在本租户内共享")
    st.info('不用写 SQL：先浏览已有记录；录入时填写中文表单，点击保存即可。带 * 的项目必填。')
    if st.session_state.pop(prefix + '_saved', False):
        st.success('已保存到 MySQL，可立即在列表中查询。')
    if st.button('刷新数据', key=prefix + '_refresh'):
        st.session_state.pop(prefix + '_snapshot', None)
        st.rerun()

    keyword = st.text_input('搜索设备、参数或内容' if equipment else '搜索设备、代码、故障现象或处理步骤',
                            key=prefix + '_search', placeholder='输入关键词后按回车，例如：功率 / RB1001')
    # Scope-wide options remain available even when a keyword matches no records.
    options_response = post(f'{backend_url}/business/{kind}/list', json={'user_ctx': ctx}, timeout=15)
    if options_response.status_code != 200:
        st.error('无法读取业务数据，请检查后端和 MySQL 服务。')
        return
    options = options_response.json()
    columns = st.columns(3)
    device = columns[0].selectbox('筛选设备', ['全部'] + options['devices'], key=prefix + '_device')
    severity = columns[1].selectbox('筛选严重程度', ['全部', '高', '中', '低'], key=prefix + '_severity') if not equipment else '全部'
    filter_key = (keyword, device, severity)
    if st.session_state.get(prefix + '_filter') != filter_key:
        st.session_state[prefix + '_page'] = 1
        st.session_state[prefix + '_filter'] = filter_key
    page = columns[2].number_input('页码（每页 50 条）', min_value=1, step=1, key=prefix + '_page')
    response = post(f'{backend_url}/business/{kind}/list', json={'user_ctx': ctx,
        'keyword': keyword, 'device': '' if device == '全部' else device,
        'severity': '' if severity == '全部' else severity, 'page': int(page)}, timeout=15)
    if response.status_code != 200:
        st.error('查询失败，请稍后重试。')
        return
    data = response.json()
    rows = data['items']
    metrics = st.columns(3)
    metrics[0].metric('当前范围记录数', options['total'])
    metrics[1].metric('设备种类', len(options['devices']))
    metrics[2].metric('筛选结果', data['total'])
    st.caption(f"第 {data['page']} / {max(1, (data['total'] + 49) // 50)} 页。已有带“演示数据”的记录仅供测试，实际参数和维修步骤请由专业人员核实。")
    writable = ctx['role'] in ('工程师', '专家')
    tabs = st.tabs(['浏览记录', '新增记录', '修改记录'] if writable else ['浏览记录'])
    with tabs[0]:
        if not rows:
            st.info('没有匹配记录，可以更换筛选条件。')
        elif equipment:
            st.dataframe([{LABELS[k]: row[k] for k in LABELS if k in row} for row in rows],
                         use_container_width=True, hide_index=True)
        else:
            for row in rows:
                with st.expander(f"{row['device_type']} · {row['code']} · 严重程度：{row['severity']}"):
                    st.markdown('**故障现象**')
                    st.text(row['description'])
                    st.markdown('**处理步骤与注意事项**')
                    st.text(row['solution'])
    if not writable:
        st.caption('当前角色为操作员，仅可查看。新增或修改请联系工程师 / 专家。')
        return
    with tabs[1]:
        st.subheader('添加设备参数' if equipment else '添加故障经验')
        st.caption('设备参数自动归属当前产线。相同设备可以填写多个不同参数。' if equipment
                   else '填写设备类型、故障代码、现象和处理步骤。同一设备类型的代码不能重复。')
        _form(kind, prefix + '_new', {}, ctx, backend_url, post, prefix)
    with tabs[2]:
        st.caption('先用上方筛选找到记录，再选择要修改的条目。修改前请核对适用设备。')
        if not rows:
            st.info('当前没有可编辑的记录。')
            return
        by_id = {row['id']: row for row in rows}
        selected = st.selectbox('选择要修改的记录', list(by_id),
            format_func=lambda rid: (f"{by_id[rid]['device_name']} / {by_id[rid]['parameter']}" if equipment
                                    else f"{by_id[rid]['device_type']} / {by_id[rid]['code']}"), key=prefix + '_edit')
        snapshot_key = prefix + '_snapshot'
        if st.session_state.get(snapshot_key, {}).get('id') != selected:
            st.session_state[snapshot_key] = by_id[selected]
        row = st.session_state[snapshot_key]
        _form(kind, prefix + '_update_' + str(selected) + row['revision'], row,
              ctx, backend_url, post, prefix)


def _form(kind, key, row, ctx, backend_url, post, prefix):
    with st.form(key):
        if kind == 'equipment':
            record = {
                'device_name': st.text_input('设备名称 *', value=row.get('device_name', ''), max_chars=200, placeholder='例如：激光焊接机'),
                'parameter': st.text_input('参数名称 *', value=row.get('parameter', ''), max_chars=100, placeholder='例如：额定功率'),
                'value': st.text_input('参数值（含单位） *', value=row.get('value', ''), max_chars=200, placeholder='例如：120 kW'),
                'tolerance': st.text_input('允许偏差 / 适用说明', value=row.get('tolerance', ''), max_chars=100, placeholder='例如：±5%；适用于某设备型号'),
            }
        else:
            record = {
                'device_type': st.text_input('设备类型 *', value=row.get('device_type', ''), max_chars=200, placeholder='例如：工业机器人'),
                'code': st.text_input('故障代码 *', value=row.get('code', ''), max_chars=100, placeholder='例如：RB1001；无厂家代码时可使用内部编号'),
                'description': st.text_area('故障现象 *', value=row.get('description', ''), max_chars=10000, placeholder='描述报警、发生条件和观察到的现象'),
                'solution': st.text_area('处理步骤与注意事项 *', value=row.get('solution', ''), max_chars=10000, height=180, placeholder='1. 安全停机与隔离\n2. 检查项目\n3. 处理方法\n4. 验证和恢复条件'),
                'severity': st.selectbox('严重程度 *', ['低', '中', '高'], index=['低', '中', '高'].index(row.get('severity', '中'))),
            }
        submitted = st.form_submit_button('保存修改' if row else '保存新增', type='primary')
    if submitted:
        required = set(record) - {'tolerance'}
        missing = [LABELS[field] for field in required if not record[field].strip()]
        if missing:
            st.error('请填写：' + '、'.join(missing))
            return
        payload = {'user_ctx': ctx, 'record': record}
        if row:
            payload.update(id=row['id'], revision=row['revision'])
        response = post(f'{backend_url}/business/{kind}/save', json=payload, timeout=20)
        if response.status_code == 200:
            st.session_state[prefix + '_saved'] = True
            st.session_state.pop(prefix + '_snapshot', None)
            st.rerun()
        else:
            detail = response.json().get('detail', '保存失败，请稍后重试。')
            st.error(detail if isinstance(detail, str) else '填写内容不符合要求，请检查必填项和长度。')
