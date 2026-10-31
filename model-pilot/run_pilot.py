"""Small, auditable model pilot with an explicit certifi TLS trust store.

No scraping, no UI changes, no automatic retries, no real credentials in outputs.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import ssl
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse, request
import certifi

ROOT = Path(__file__).resolve().parent
VERSION = 'pilot-0.2'
DIMS = {'口味', '冲调', '包装', '规格'}
ATTITUDES = {'正向', '负向', '描述性', '不明确'}


class ConfigError(ValueError):
    pass


def read_config(path, overrides=None):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    for field, env in [('api_key', 'LLM_API_KEY'), ('base_url', 'LLM_BASE_URL'), ('model', 'LLM_MODEL')]:
        data[field] = os.environ.get(env) or data.get(field, '')
    data.update({key: value for key, value in (overrides or {}).items() if value is not None})
    return validate_config(data)


def validate_config(data):
    for field in ['provider', 'base_url', 'model', 'api_key']:
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ConfigError(f'本机配置缺少 {field}，尚未发送模型请求。')
        data[field] = data[field].strip()
    key = data['api_key']
    if '*' in key or any(ch.isspace() for ch in key) or key.startswith(('YOUR_', '请填')):
        raise ConfigError('密钥含掩码、空白或占位文字，请填写完整密钥。尚未发送请求。')
    url = parse.urlsplit(data['base_url'])
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ConfigError('Base URL须为不含密钥、账号、查询参数的HTTPS地址。')
    api_format = data.get('api_format', 'chat_completions')
    if api_format not in ['chat_completions', 'responses']:
        raise ConfigError('api_format须为chat_completions或responses。')
    data['api_format'] = api_format
    suffix = '/responses' if api_format == 'responses' else '/chat/completions'
    base = data['base_url'].rstrip('/')
    # Ximu's public model-catalog helper normalizes its root URL to /v1.
    if url.hostname == 'codex.ximuai.com' and not url.path.strip('/'):
        base += '/v1'
    for known in ['/responses', '/chat/completions']:
        if base.endswith(known):
            if known != suffix:
                raise ConfigError('完整接口路径与选择的接口类型不一致。')
            break
    data['endpoint'] = base if base.endswith(suffix) else base + suffix
    if not isinstance(data.get('stream', False), bool):
        raise ConfigError('stream须为true或false。')
    if data.get('stream') and api_format != 'responses':
        raise ConfigError('当前流式解析仅支持Responses。')
    if not isinstance(data.get('json_mode', True), bool):
        raise ConfigError('json_mode须为true或false。')
    for field, low, high, default in [('max_tokens', 128, 4096, 1200), ('timeout_seconds', 5, 180, 90)]:
        value = data.get(field, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ConfigError(f'{field}须为{low}到{high}之间的整数。')
        data[field] = value
    if data.get('enable_thinking') is not None and not isinstance(data['enable_thinking'], bool):
        raise ConfigError('enable_thinking须为true、false或null。')
    temp = data.get('temperature')
    if temp is not None and (isinstance(temp, bool) or not isinstance(temp, (int, float)) or not 0 <= temp <= 2):
        raise ConfigError('temperature须为0到2的数值或null。')
    return data


def read_cases(path, limit):
    batch = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(batch, dict) or not isinstance(batch.get('cases'), list) or not batch['cases']:
        raise ConfigError('案例文件需要非空cases数组。')
    ids = set()
    for item in batch['cases']:
        if not isinstance(item, dict) or not isinstance(item.get('review_id'), str) or not item['review_id']:
            raise ConfigError('案例缺少评价编号。')
        if item['review_id'] in ids:
            raise ConfigError('案例评价编号重复。')
        ids.add(item['review_id'])
        if not isinstance(item.get('main_text'), str) or not item['main_text'].strip():
            raise ConfigError('案例正文不能为空。')
        if not isinstance(item.get('followup_text', ''), str):
            raise ConfigError('追评必须为文字。')
        if len(item['main_text']) + len(item.get('followup_text', '')) > 10000:
            raise ConfigError('单案例超过10000字符，请先核查材料。')
    result = copy.deepcopy(batch)
    result['cases'] = result['cases'][:limit]
    return result


def payload_for(config, case, prompt):
    # Evaluation focus stays with the human reviewer and is not disclosed to model.
    input_data = {k: case.get(k, '') for k in ['review_id', 'main_text', 'followup_text']}
    if config.get('api_format') == 'responses':
        payload = {'model': config['model'], 'instructions': prompt,
                   'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': json.dumps(input_data, ensure_ascii=False)}]}],
                   'max_output_tokens': config['max_tokens'], 'store': False,
                   'stream': config.get('stream', False)}
        if config.get('json_mode', True):
            payload['text'] = {'format': {'type': 'json_object'}}
        if config.get('temperature') is not None:
            payload['temperature'] = config['temperature']
        return payload
    payload = {'model': config['model'], 'messages': [
        {'role': 'system', 'content': prompt},
        {'role': 'user', 'content': json.dumps(input_data, ensure_ascii=False)}
    ], 'max_tokens': config['max_tokens'], 'stream': False}
    if config.get('json_mode', True):
        payload['response_format'] = {'type': 'json_object'}
    for name in ['enable_thinking', 'temperature']:
        if config.get(name) is not None:
            payload[name] = config[name]
    return payload


def validate_output(data, case):
    issues = []
    if not isinstance(data, dict):
        return ['顶层不是JSON对象']
    if data.get('review_id') != case['review_id']:
        issues.append('评价编号不匹配')
    items = data.get('feedback')
    if not isinstance(items, list):
        return issues + ['feedback不是数组']
    if len(items) > 12:
        issues.append('单条评价反馈超过12项，需检查重复提取')
    notes = data.get('scope_notes')
    if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
        issues.append('scope_notes须为文字数组')
    for i, item in enumerate(items):
        prefix = f'反馈{i+1}：'
        if not isinstance(item, dict):
            issues.append(prefix + '不是对象')
            continue
        if item.get('dimension') not in DIMS:
            issues.append(prefix + '维度无效')
        if item.get('attitude') not in ATTITUDES:
            issues.append(prefix + '态度无效')
        if not isinstance(item.get('summary'), str) or not item['summary'].strip():
            issues.append(prefix + '摘要为空或类型错误')
        stage = item.get('stage')
        text = case.get('main_text' if stage == 'main' else 'followup_text', '')
        if stage not in ['main', 'followup']:
            issues.append(prefix + '发表阶段无效')
        evidence = item.get('evidence')
        if not isinstance(evidence, str) or not evidence.strip() or evidence not in text:
            issues.append(prefix + '依据未在对应阶段原文中找到')
        if not isinstance(item.get('needs_review'), bool):
            issues.append(prefix + '复核标记不是布尔值')
        if not isinstance(item.get('review_reason'), str):
            issues.append(prefix + '复核原因类型错误')
        elif item.get('needs_review') is True and not item['review_reason'].strip():
            issues.append(prefix + '需复核但未说明原因')
    return issues


def safe_message(message, key):
    text = str(message).replace(key, '[REDACTED]') if key else str(message)
    text = re.sub(r'sk-[A-Za-z0-9_.-]+', '[REDACTED]', text)
    return text[:500]


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward an authorization header to a redirect target.


def parse_api_body(body, content_type):
    text = body.decode('utf-8-sig')
    if 'text/event-stream' not in content_type and not text.lstrip().startswith(('event:', 'data:')):
        return json.loads(text)
    terminal = None
    for block in text.replace('\r\n', '\n').split('\n\n'):
        values = [line[5:].lstrip() for line in block.split('\n') if line.startswith('data:')]
        if not values:
            continue
        raw = '\n'.join(values)
        if raw.strip() == '[DONE]':
            continue
        event = json.loads(raw)
        if event.get('type') in ['response.completed', 'response.incomplete', 'response.failed']:
            terminal = event.get('response')
        elif event.get('type') == 'error':
            raise ValueError(str(event.get('message', '流式接口返回错误。')))
    if not isinstance(terminal, dict):
        raise ValueError('流式响应缺少终态，不能把中间片段当成完整结果。')
    return terminal


def extract_answer(response, api_format):
    if not isinstance(response, dict):
        raise ValueError('接口返回值不是JSON对象。')
    if api_format == 'responses':
        if response.get('error'):
            err = response['error']
            raise ValueError(str(err.get('message', 'Responses执行失败。')) if isinstance(err, dict) else 'Responses执行失败。')
        parts = []
        for item in response.get('output', []):
            if isinstance(item, dict) and item.get('type') == 'message':
                for content in item.get('content', []):
                    if isinstance(content, dict) and content.get('type') == 'output_text':
                        parts.append(content.get('text', ''))
        text = ''.join(parts)
        if not text:
            raise ValueError('Responses没有回答正文；推理或工具事件不能当作答案。')
        finish = 'stop' if response.get('status') == 'completed' else 'incomplete'
        return text, finish
    choices = response.get('choices')
    if not choices:
        raise ValueError('响应缺少choices，不是预期的Chat Completions格式。')
    return choices[0].get('message', {}).get('content'), choices[0].get('finish_reason')


def invoke(config, payload):
    req = request.Request(config['endpoint'], data=json.dumps(payload, ensure_ascii=False).encode(),
                          headers={'Authorization': 'Bearer ' + config['api_key'], 'Content-Type': 'application/json'}, method='POST')
    # Use the maintained CA bundle instead of expired roots in a local Windows
    # certificate store. Hostname and certificate-chain verification stay on.
    tls = ssl.create_default_context(cafile=certifi.where())
    opener = request.build_opener(NoRedirect(), request.HTTPSHandler(context=tls))
    try:
        with opener.open(req, timeout=config['timeout_seconds']) as response:
            body = response.read(4_000_001)
            if len(body) > 4_000_000:
                raise ValueError('接口返回内容过大，已停止解析。')
            return parse_api_body(body, response.headers.get('Content-Type', ''))
    except error.HTTPError as exc:
        body = exc.read(16000).decode('utf-8', errors='replace')
        details = ''
        try:
            data = json.loads(body)
            item = data.get('error', data)
            if isinstance(item, dict):
                details = str(item.get('code', '')) + ' ' + str(item.get('message', ''))
        except (ValueError, AttributeError):
            pass
        raise RuntimeError(f'HTTP {exc.code}: ' + safe_message(details or '接口未接受请求。', config['api_key'])) from None


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def md_text(value):
    # Preserve model text as text rather than executable HTML in the report.
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '\\|')


def build_report(run):
    lines = ['# 少量案例真实模型试运行', '', f"状态：{run['status']}。案例来源：{run['source_type']}。", '',
             '结构/引用检查不是语义准确率；本报告尚未经人工评审。未保存模型推理过程。', '',
             f"请求数：{len(run['results'])}；程序版本：{VERSION}；模型：{md_text(run['model'])}。", '']
    for item in run['results']:
        case = item['input']
        lines += [f"## {md_text(case['review_id'])}", '', '初评：' + md_text(case['main_text']), '']
        if case.get('followup_text'):
            lines += ['追评：' + md_text(case['followup_text']), '']
        lines += ['状态：' + item['status'], f"耗时：{item['elapsed_seconds']}秒。", '']
        if item.get('error'):
            lines += ['错误：' + md_text(item['error']), '']
        parsed = item.get('parsed')
        if isinstance(parsed, dict) and isinstance(parsed.get('feedback'), list):
            for f in parsed['feedback']:
                if isinstance(f, dict):
                    lines += [f"- {md_text(f.get('dimension',''))} / {md_text(f.get('attitude',''))}：{md_text(f.get('summary',''))}",
                              f"  原文依据（{md_text(f.get('stage',''))}）：{md_text(f.get('evidence',''))}"]
            lines += ['']
        if item.get('validation_errors'):
            lines += ['机械校验问题：' + md_text('；'.join(item['validation_errors'])), '']
        if case.get('focus'):
            lines += ['人工核对重点（未发送给模型）：' + md_text(case['focus']), '']
        lines += ['人工结论：尚未评审。', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='少量案例真实模型调用；最多10条，无自动重试。')
    parser.add_argument('--config', type=Path, default=ROOT / 'config.local.json')
    parser.add_argument('--cases', type=Path, default=ROOT / 'cases.json')
    parser.add_argument('--limit', type=int, default=1)
    parser.add_argument('--model', help='本次试运行模型名，覆盖配置文件中的旧示例；不涉及密钥。')
    parser.add_argument('--base-url', help='本次试运行的服务地址，仍须通过HTTPS配置校验。')
    parser.add_argument('--check', action='store_true', help='只检查配置和输入，不发送请求。')
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.limit <= 10:
            raise ConfigError('limit须为1到10。')
        config = read_config(args.config, {'model': args.model, 'base_url': args.base_url})
        batch = read_cases(args.cases, args.limit)
    except (OSError, ValueError) as exc:
        print('未调用模型：' + str(exc))
        return 2
    print(f"配置检查通过；案例数{len(batch['cases'])}；密钥已配置（不显示）。")
    if args.check:
        print('仅检查，未发送请求，未产生本程序的模型调用费用。')
        return 0
    prompt = (ROOT / 'prompt.txt').read_text(encoding='utf-8')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    target = ROOT / 'runs' / stamp
    target.mkdir(parents=True)
    run = {'version': VERSION, 'started_utc': datetime.now(timezone.utc).isoformat(),
           'status': 'running', 'provider': config['provider'], 'model': config['model'],
           'api_format': config['api_format'],
           'endpoint_host': parse.urlsplit(config['endpoint']).hostname,
           'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
           'source_type': batch.get('source_type', 'unspecified'), 'source_note': batch.get('note', ''),
           'results': []}
    write_json(target / 'run.json', run)
    for case in batch['cases']:
        start = time.monotonic()
        item = {'input': case, 'status': 'pending', 'validation_errors': []}
        try:
            response = invoke(config, payload_for(config, case, prompt))
            content, finish = extract_answer(response, config['api_format'])
            item['finish_reason'] = finish
            item['usage'] = response.get('usage')
            item['request_id'] = response.get('id')
            item['returned_model'] = response.get('model')
            item['raw_content'] = content
            if not isinstance(content, str) or not content.strip():
                raise ValueError('回答正文为空。')
            parsed = json.loads(content)
            item['parsed'] = parsed
            item['validation_errors'] = validate_output(parsed, case)
            if item['finish_reason'] in ['length', 'incomplete']:
                item['validation_errors'].append('输出截断或未完成，不能视为完整结果')
            item['status'] = 'needs_validation_fix' if item['validation_errors'] else 'awaiting_human_review'
        except (ValueError, RuntimeError, OSError, KeyError, TypeError, AttributeError, IndexError) as exc:
            item['status'] = 'failed'
            item['error'] = safe_message(exc, config['api_key'])
        item['elapsed_seconds'] = round(time.monotonic() - start, 3)
        run['results'].append(item)
        write_json(target / 'run.json', run)
        print(f"{case['review_id']}: {item['status']} ({item['elapsed_seconds']}s)", flush=True)
        # Stop credential, billing and interface failures instead of repeating them on every case.
        if item['status'] == 'failed' and any(code in item.get('error', '') for code in ['HTTP 400', 'HTTP 401', 'HTTP 402', 'HTTP 403', 'HTTP 404', 'HTTP 429']):
            break
    run['status'] = 'completed_with_issues' if len(run['results']) != len(batch['cases']) or any(r['status'] != 'awaiting_human_review' for r in run['results']) else 'awaiting_human_review'
    run['finished_utc'] = datetime.now(timezone.utc).isoformat()
    write_json(target / 'run.json', run)
    (target / '人工复盘.md').write_text(build_report(run), encoding='utf-8')
    print('结果已保存：' + str(target))
    print('本程序未自动重试；实际费用请核对服务商账单。')
    return 0 if run['status'] == 'awaiting_human_review' else 1


if __name__ == '__main__':
    raise SystemExit(main())
