"""Add fictional industrial content without replacing any existing business rows."""
import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import sys

import pymysql
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo_content import LINES, business_rows, knowledge_documents, DEVICES

TABLES = ('tenants', 'plants', 'production_lines', 'device_parameters', 'fault_knowledge',
          'process_documents', 'rag_document_versions')


def connect():
    load_dotenv(ROOT / '.env')
    return pymysql.connect(host=os.getenv('MYSQL_HOST', '127.0.0.1'), port=int(os.getenv('MYSQL_PORT', '3306')),
        user=os.getenv('MYSQL_USER', 'factory_app'), password=os.getenv('MYSQL_PASSWORD', ''),
        database=os.getenv('MYSQL_DATABASE', 'factory_kb'), charset='utf8mb4', connect_timeout=5,
        read_timeout=30, write_timeout=30, autocommit=False)


def counts(conn):
    result = {}
    with conn.cursor() as cursor:
        for table in TABLES:
            cursor.execute('SELECT COUNT(*) FROM ' + table)
            result[table] = cursor.fetchone()[0]
    return result


def insert_missing(cursor, table, columns, key_columns, rows):
    added = 0
    indexes = [columns.index(key) for key in key_columns]
    where = ' AND '.join(key + '=%s' for key in key_columns)
    for row in rows:
        cursor.execute('SELECT 1 FROM ' + table + ' WHERE ' + where + ' FOR UPDATE', [row[index] for index in indexes])
        if cursor.fetchone():
            continue
        cursor.execute('INSERT INTO ' + table + ' (' + ','.join(columns) + ') VALUES (' + ','.join(['%s'] * len(row)) + ')', row)
        added += 1
    return added


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect', action='store_true', help='只查看业务表数量及产线范围')
    parser.add_argument('--with-knowledge', action='store_true', help='同时将16份演示版本资料上传到本地知识库')
    parser.add_argument('--username', help='当前产线的专家账号；知识资料仅导入该账号的访问范围')
    args = parser.parse_args()
    if args.with_knowledge and not args.username:
        parser.error('--with-knowledge 需要 --username，密码通过隐藏提示输入')
    conn = connect()
    try:
        before = counts(conn)
        with conn.cursor() as cursor:
            cursor.execute('SELECT l.line_id,l.plant_id,p.tenant_id,l.name FROM production_lines l JOIN plants p ON p.plant_id=l.plant_id ORDER BY l.line_id')
            lines = cursor.fetchall()
        conn.rollback()
        if args.inspect:
            print(json.dumps({'counts': before, 'lines': lines}, ensure_ascii=True, indent=2))
            return
        present = {row[0]: (row[2], row[1]) for row in lines}
        for line, (tenant, plant, _) in LINES.items():
            if present.get(line) != (tenant, plant):
                raise RuntimeError(f'{line} scope differs from demo fixture; no business data written')
        if args.with_knowledge:
            login_response = requests.post('http://127.0.0.1:8000/auth/login',
                json={'username': args.username, 'password': getpass('专家密码: ')}, timeout=15)
            if not login_response.ok:
                raise RuntimeError(f'专家登录失败: HTTP {login_response.status_code}')
            login = login_response.json()
            ctx = login['user_ctx']
            if ctx['role'] != '专家':
                raise RuntimeError('知识导入需要专家账号')
            headers = {'Authorization': 'Bearer ' + login['access_token']}
        parameters, faults = business_rows()
        with conn.cursor() as cursor:
            added_parameters = insert_missing(cursor, 'device_parameters',
                ['line_id', 'device_name', 'parameter', 'value', 'tolerance'], ['line_id', 'device_name', 'parameter'], parameters)
            added_faults = insert_missing(cursor, 'fault_knowledge',
                ['tenant_id', 'device_type', 'code', 'description', 'solution', 'severity'], ['tenant_id', 'device_type', 'code'], faults)
        conn.commit()
        print(json.dumps({'added_parameters': added_parameters, 'added_faults': added_faults,
                          'fixture_devices': len(DEVICES)}, ensure_ascii=True), flush=True)
        uploaded, added_catalog = 0, 0
        if args.with_knowledge:
            for document in knowledge_documents():
                if (document['tenant'], document['plant'], document['line']) != (
                    ctx['tenant_id'], ctx['plant_id'], ctx['line_id']):
                    continue
                response = requests.post('http://127.0.0.1:8000/upload-document', headers=headers, data={
                    'user_ctx': json.dumps(ctx, ensure_ascii=False), 'title': document['family'],
                    'category': document['category'], 'access_level': document['access'], 'family_name': document['family'],
                    'version': document['version'], 'effective_from': document['start'], 'publish': 'true'},
                    files={'file': (f"{document['family']}-{document['version']}.md", document['content'].encode('utf-8'))}, timeout=600)
                if not response.ok:
                    raise RuntimeError(f"Knowledge import {document['line']} {document['version']}: HTTP {response.status_code}")
                result = response.json()
                with conn.cursor() as cursor:
                    added_catalog += insert_missing(cursor, 'process_documents',
                        ['doc_id', 'line_id', 'title', 'category', 'access_level'], ['doc_id'],
                        [(result['document_id'], document['line'], document['family'] + ' ' + document['version'],
                          document['category'], document['access'])])
                conn.commit()
                uploaded += 1
                print(f"Knowledge {uploaded}: {document['line']} {document['version']}; chunks={result['chunks']}; added={result['added_chunks']}", flush=True)
        after = counts(conn)
        print(json.dumps({'before': before, 'after': after, 'knowledge_documents_processed': uploaded,
            'added_process_catalog': added_catalog}, ensure_ascii=True, indent=2))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
