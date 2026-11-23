"""Derive starter questions from the current reading material."""
from itertools import chain
import re

from zhihu_agent.corpus import tokenize

from .library_research import passages

CODE_BLOCK = re.compile(r'^[ \t]*\x60{3,}[^\n]*\n[\s\S]*?^[ \t]*\x60{3,}[^\n]*$', re.MULTILINE)
HEADINGS = re.compile(r'^ {0,3}#{1,6}[ \t]+([^\n]+)', re.MULTILINE)
EMPHASIS = re.compile(r'\*\*([^\n*]{4,40})\*\*')
LINK = re.compile(r'\[([^\[\]]+)\]\([^\n)]+\)')
LIMIT = 3


def clean_topic(value, limit=54):
    text = LINK.sub(r'\1', value)
    text = re.sub(r'https?://\S+', '', text)
    return ' '.join(text.split()).strip(' #*\x60-:：。')[:limit].rstrip()


def article_topics(document):
    title = clean_topic(document['title'])
    body = CODE_BLOCK.sub('', document['body'][:16000])
    seen = {title.casefold(), '目录', '参考资料', '参考文献', '相关阅读', '总结', '前言', '结语'}
    candidates = HEADINGS.findall(body) + EMPHASIS.findall(body)
    candidates.extend(re.split(r'[，。！？；：,.!?;:\n]', paragraph, maxsplit=1)[0]
                      for _, paragraph in passages(body) if not paragraph.startswith(('我', '你', '其实', '有人说')))
    for candidate in candidates:
        topic = clean_topic(candidate)
        key = topic.casefold()
        if 4 <= len(topic) <= 48 and key not in seen and tokenize(topic) and not topic.startswith('!['):
            seen.add(key)
            yield topic


def topic_question(topic):
    if topic.endswith(('？', '?')):
        return topic
    if re.search(r'局限|风险|限制|注意事项|前提', topic):
        return f'文中提到了「{topic}」的哪些注意点？'
    if re.search(r'步骤|实现|流程|方法|配置|实践', topic):
        return f'文中关于「{topic}」给出了哪些做法？'
    return f'文中是如何说明「{topic}」的？'


def suggest_questions(documents):
    if not documents:
        return []
    suggestions = []
    used = set()
    per_document = LIMIT if len(documents) == 1 else 1
    for document in documents[:LIMIT]:
        title = clean_topic(document['title'])
        overview = f'「{title}」主要讲了什么？' if title else ''
        is_answer = 'id' in document.keys() and document['id'].startswith('answer-')
        if is_answer:
            overview = '这篇知乎回答的主要观点是什么？'
        topics = (topic_question(topic) for topic in article_topics(document))
        options = (chain([overview], topics, ['作者给出了哪些理由？'] if is_answer else []) if per_document > 1
                   else [next(topics, overview), overview])
        picked = 0
        for text in options:
            if text and text not in used:
                used.add(text)
                suggestions.append({'text': text, 'source_title': document['title']})
                picked += 1
                if picked == per_document:
                    break
    return suggestions
