"""Small, reproducible semantic relevance check with hand-written public-safe titles."""
import argparse
import json
from math import log2
from pathlib import Path
from time import perf_counter

from .embeddings import CACHE, LocalEncoder, encoder


CASES = [
    ('LangGraph 的任务检查点与故障恢复', [
        (0, 'LangGraph 检查点设计面试题汇总'), (0, 'Python 保存模型权重与检查点'),
        (2, '智能体执行到一半崩了，如何从原步骤接着跑'), (1, '工作流故障恢复的几种策略'),
        (2, '用持久化状态实现 LangGraph 断点续跑'), (0, '旅行路线上的打卡检查点')]),
    ('Python 异步接口处理超时和取消', [
        (1, 'FastAPI 性能调优：并发和线程池'), (0, '异步接口的字段命名规范'),
        (2, '如何给协程设置截止时间并终止未完成的任务'), (0, '客服工单超时以后如何取消预约'),
        (2, 'asyncio.timeout 和 CancelledError 的使用'), (0, 'Python 变量作用域')]),
    ('向量召回以后怎么关联知识图谱中的实体', [
        (0, '给知识图谱画一个交互式页面'), (2, '从检索到的段落定位人名，再沿关系边扩展证据'),
        (1, '检索增强中的多跳问答'), (0, '数学中的向量投影方法'),
        (2, '混合 RAG：把向量召回结果映射到图节点'), (0, '知识图谱实体名称排版')]),
    ('大模型推理的 KV cache 如何节省显存', [
        (0, 'KV 存储缓存的过期策略'), (2, '解码时复用注意力键值张量，省掉重复计算与存储'),
        (1, '连续批处理提高推理并发'), (2, '降低 KV cache 显存占用的常见方法'),
        (0, '挑选显卡时怎样看显存容量'), (0, '离线训练日志如何滚动归档')]),
    ('SQLite 读写并发和 WAL', [
        (0, 'SQLite 查询结果怎样导出为 CSV'), (1, '数据库锁冲突的排查'),
        (2, '写入追加到日志，读取继续使用旧快照'), (0, '键盘的读写寿命测试'),
        (2, 'SQLite WAL 模式为什么允许读写并行'), (0, '如何为接口生成 OpenAPI 文档')]),
    ('推荐系统如何避免把没看到的内容当负样本', [
        (0, '推荐系统面试里常见的负样本术语'), (2, '曝光日志和未展示商品的标签污染'),
        (2, '隐式反馈排序中区分未曝光、跳过和明确不喜欢'), (0, '正负样本拍摄的补光技巧'),
        (1, '列表位置对点击率的影响'), (0, '推荐系统候选去重')]),
    ('前端虚拟列表优化长列表滚动', [
        (2, '只渲染视口里看得见的行，减少 DOM 数量'), (0, '虚拟机里鼠标滚动异常'),
        (1, 'Vue 前端组件渲染性能排查'), (2, '长列表为什么需要窗口化渲染'),
        (0, '前端下拉列表应该怎么命名'), (0, '页面滚动条的样式设置')]),
    ('Python 单元测试如何隔离外部 API', [
        (0, 'Python 如何调第三方 API'), (2, '用桩替代网络请求，使回归测试可重复'),
        (1, 'Web 服务端到端测试的划分'), (2, 'pytest 用 monkeypatch 模拟第三方接口返回'),
        (0, 'API 网关的速率限制'), (0, '测试环境和生产环境的域名管理')]),
    ('容器应用的健康检查与就绪探针', [
        (0, '给开发者的久坐健康检查'), (2, '让还在启动的服务先不接流量，死锁时再重启'),
        (1, 'Kubernetes 常用部署配置'), (2, '区分 liveness 与 readiness 以及启动探针'),
        (0, '用容器批量打包静态文件'), (0, '系统监控图表颜色怎么选')]),
    ('离线排序评估的时间切分和泄漏', [
        (0, '商场空调管路泄漏排查'), (2, '用过去的行为预测后来的候选，别提前使用未来互动'),
        (1, 'NDCG 怎样评价前几条的相关程度'), (2, '训练推荐模型时按时间留出集，避免数据泄漏'),
        (0, '不同软件的排序功能怎么设置'), (0, '排序算法选择与复杂度')]),
]


def dcg(ordered):
    return sum((2 ** grade - 1) / log2(index + 2) for index, grade in enumerate(ordered[:3]))


def evaluate(label, scorer):
    examples = []
    started = perf_counter()
    for profile, candidates in CASES:
        grades, titles = zip(*candidates, strict=True)
        values = scorer(profile, titles)
        ranked = sorted(range(len(titles)), key=lambda index: (-values[index], index))
        examples.append({
            'profile': profile, 'ndcg_at_3': round(dcg([grades[i] for i in ranked]) / dcg(sorted(grades, reverse=True)), 6),
            'first_grade': grades[ranked[0]],
            'top3': [{'title': titles[i], 'grade': grades[i], 'score': round(float(values[i]), 6)} for i in ranked[:3]],
        })
    return {'model': label, 'mean_ndcg_at_3': round(sum(row['ndcg_at_3'] for row in examples) / len(examples), 4),
            'top1_grade2': sum(row['first_grade'] == 2 for row in examples),
            'warm_ms_per_7_titles': round((perf_counter() - started) * 1000 / len(examples), 1),
            'examples': examples}


def compare():
    parser = argparse.ArgumentParser()
    parser.add_argument('--e5-dir', type=Path, help='Directory holding e5-quant.onnx and e5-tokenizer.json')
    args = parser.parse_args()
    from .serve import configure_imports

    configure_imports()
    from zhihu_agent.recommend import Preferences
    from .discover_preferences import similarity
    from .pairwise_ranker import PRIOR, cosine_feature
    import numpy as np

    def lexical(profile, titles):
        terms = set(Preferences.terms(profile))
        return [similarity(terms, set(Preferences.terms(title))) for title in titles]

    def benchmark_model(label, load, file, *, profile_prefix=''):
        started = perf_counter()
        model = load()
        init = perf_counter() - started
        model.embed(['一次预热'])

        def cosine(profile, titles):
            head, *rest = model.embed([profile_prefix + profile, *titles])
            return np.asarray(rest) @ np.asarray(head)

        return {**evaluate(label, cosine), 'cold_load_ms': round(init * 1000, 1), 'onnx_bytes': file.stat().st_size}

    results = [evaluate('title-term cosine', lexical),
               benchmark_model('BAAI/bge-small-zh-v1.5 (int8)', encoder, CACHE / 'model.onnx'),
               benchmark_model('BAAI/bge-small-zh-v1.5 (int8, profile instruction)', encoder, CACHE / 'model.onnx',
                               profile_prefix='为这个句子生成表示以用于检索相关文章：')]
    def hybrid(profile, titles):
        head, *rest = encoder().embed([profile, *titles])
        semantic = np.asarray(rest) @ np.asarray(head)
        return [term + PRIOR[1] * cosine_feature(cosine) for term, cosine in zip(lexical(profile, titles), semantic, strict=True)]

    results.append(evaluate('lexical + capped BGE feature', hybrid))
    if args.e5_dir:
        e5 = args.e5_dir / 'e5-quant.onnx'
        tokenizer = args.e5_dir / 'e5-tokenizer.json'
        results.append(benchmark_model('intfloat/multilingual-e5-small (int8)',
                                       lambda: LocalEncoder(e5, tokenizer, pool='mean', prefix='query: '), e5))
    print(json.dumps({'label_source': 'synthetic_semantic_fixture', 'batches': len(CASES), 'results': results},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    compare()
