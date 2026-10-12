"""Authenticated Streamlit requests; never share tokens across browser sessions."""
import json
import requests
import streamlit as st


def auth_headers():
    token = st.session_state.get('access_token')
    return {'Authorization': f'Bearer {token}'} if token else {}


def clear_identity():
    # Includes conversation, cached account lists, forms and file uploads.
    st.session_state.clear()


def request(method, url, **kwargs):
    kwargs['headers'] = {**kwargs.pop('headers', {}), **auth_headers()}
    try:
        response = requests.request(method, url, **kwargs)
    except requests.RequestException:
        response = requests.Response()
        response.status_code = 503
        response._content = json.dumps({'detail': '后端服务暂不可用，请检查服务状态。'}, ensure_ascii=False).encode()
        response.encoding = 'utf-8'
    if response.status_code == 401 and not url.endswith('/auth/login'):
        clear_identity()
        st.rerun()
    return response


def post_backend(url, **kwargs):
    return request('POST', url, **kwargs)


def get_backend(url, **kwargs):
    return request('GET', url, **kwargs)
