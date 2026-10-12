"""Local conversational intents must never be rewritten into technical searches."""
import re
import time


def direct_intent(question):
    q = re.sub(r'[\s，。！？、,.!?：:～~]+', '', question).lower()
    if re.fullmatch(r'(你好|您好|哈喽|嗨|hello|hi|早上好|晚上好|下午好|早安|晚安|在吗|你在吗|谢谢|谢谢你|感谢|多谢|再见|拜拜|好的|好吧|收到|明白了|了解了|ok|okay)+', q):
        return 'chat'
    if re.fullmatch(r'(你是谁|你是什么|你能做什么|你有什么功能|介绍一下你自己|你是干什么的|如何使用你)', q):
        return 'about'
    if re.fullmatch(r'(你还记得)?(我|我们)(刚刚|刚才|之前|上次|上一轮|前面|最近)?(都)?(问|问了|问过|问的|问了些|说了|说过|说的|聊了|聊过)(什么|啥|哪些问题|什么问题|些什么|些啥)(吗)?', q):
        return 'history'
    if re.fullmatch(r'(回顾|总结)(一下)?(我们|之前|刚才|前面|最近|这段|本次|当前)*(的)?(对话|聊天|聊天记录|问题|提问)(内容)?', q):
        return 'history'
    if re.fullmatch(r'(你)(刚刚|刚才|之前|上一轮)(说了|说的|回答了|回答的)(什么|啥|什么内容)', q):
        return 'history_answer'
    return None


def local_answer(intent, question, history):
    if intent == 'about':
        return '我是智能工厂知识助手，可以查询设备参数、故障资料和工艺标准，核对文档版本，也能回顾当前会话。你可以告诉我设备名称和具体问题。'
    if intent == 'chat':
        if re.search(r'谢|感谢', question):
            return '不客气！有设备、故障或工艺方面的问题可以继续问我。'
        if re.search(r'再见|拜拜|晚安', question):
            return '再见，需要帮助时随时回来。'
        if re.search(r'好的|收到|明白|了解|ok', question, re.I):
            return '好的，有新的问题可以继续告诉我。'
        return '你好！我在。你可以咨询设备、故障或工艺问题，也可以让我回顾我们刚才的对话。'
    if intent == 'history_answer':
        items = [item for item in history['messages'] if item['role'] == 'assistant']
        return '我上一条回复是：\n\n' + items[-1]['content'] if items else '当前会话中还没有此前的回复。'
    items = [item for item in history['messages'] if item['role'] == 'user']
    if not items:
        return '当前会话中还没有此前的提问。其他会话的记录需要先恢复对应会话。'
    if re.search(r'总结|回顾|聊|哪些|问过|之前|最近|前面', question):
        return '当前会话最近的提问如下：\n\n' + '\n\n'.join(f'{index}. {item["content"]}' for index, item in enumerate(items[-10:], 1))
    return '你上一条消息是：\n\n' + items[-1]['content']


def conversational_result(sid, answer, intent, state, started):
    return {'session_id': sid, 'intent': 'history' if intent.startswith('history') else 'chat',
        'answer': answer, 'sources': [], 'source_data': [], 'citation_valid': False,
        'confidence': {}, 'retrieval_steps': [], 'diagnostics': {
            'route': 'history' if intent.startswith('history') else 'chat',
            'memory_backend': state.cache_status, 'total_ms': round((time.monotonic() - started) * 1000)}}


def direct_query(state, question, ctx, session_id=None):
    started = time.monotonic()
    intent = direct_intent(question)
    with state.conversation(ctx, session_id) as (sid, token, history):
        result = conversational_result(sid, local_answer(intent, question, history), intent, state, started)
        state.save_turn(ctx, sid, token, question, result)
        return result
