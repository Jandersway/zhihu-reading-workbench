"""Detect platform ads and explicit commercial calls to action in the home feed."""
from html import unescape
import re


AD_TYPES = {'ad', 'advertisement', 'promotion', 'promoted', 'sponsored', 'commercial'}
AD_LABELS = AD_TYPES | {'广告', '商业推广', '赞助内容'}
DISCLOSURE = re.compile(r'^(?:[【\[（(]\s*(?:广告|商业推广|赞助内容)\s*[】\]）)]|'
                        r'(?:本文|本内容)(?:为(?:商业)?推广内容|由[^，。\r\n]{1,40}赞助))')
SALES = re.compile(r'购买|下单|领券|优惠券|付费|报价|报名|预约(?:咨询|问诊)|(?:一对一|1对1)咨询|训练营|佣金|专享价')
CALL_TO_ACTION = re.compile(r'点击(?:下方|文末|我的|商品|购买|店铺|主页)?链接|'
                            r'扫码|加(?:我的|我)?(?:微信|企微)|添加(?:我的|我)?(?:微信|企微)|'
                            r'私信我|私信回复|回复(?:关键词|暗号)|评论区置顶')
PITCH = re.compile(r'总卡平台.{0,60}其实|原因找不到.{0,30}其实')
CONSULTATION = re.compile(r'在咨询中|在接诊中|咨询我的|我的学员|我的客户')
MARKUP = re.compile(r'<[^>]*>')


def marker(value):
    return value.strip().casefold() if isinstance(value, str) else ''


def plain(value, limit):
    return MARKUP.sub(' ', unescape(value[:limit])).strip() if isinstance(value, str) else ''


def commercial_pitch(item):
    for field in ('excerpt', 'content', 'content_html'):
        value = item.get(field)
        if not isinstance(value, str):
            continue
        # Require a selling action in the same short passage. A mention of a service alone is insufficient.
        text = plain(re.sub(r'</(?:p|li|div)>|<br\s*/?>', '\n', value[:12000], flags=re.I), 12000)
        for paragraph in re.split(r'[\n。！？!?]+', text):
            for action in CALL_TO_ACTION.finditer(paragraph):
                window = paragraph[max(0, action.start() - 50):action.end() + 50]
                if SALES.search(window) and not re.search(r'不要|谨防|请勿|警惕|不要相信|骗局', window):
                    return True
    return False


def suspected_promotion(title, excerpt):
    return bool(PITCH.search(title) and CONSULTATION.search(plain(excerpt, 600)))


def is_promotion(row):
    if not isinstance(row, dict):
        return False
    for item in (row, row.get('target')):
        if not isinstance(item, dict):
            continue
        if item.get('promotion_filtered') is True:
            return True
        if any(item.get(key) is True for key in ('is_ad', 'is_advertisement', 'is_promoted', 'is_sponsored')):
            return True
        if any(marker(item.get(key)) in AD_TYPES for key in ('type', 'target_type', 'verb')):
            return True
        if any(isinstance(item.get(key), dict) and item[key] for key in ('ad_info', 'advertisement_info')):
            return True
        for field in ('label', 'label_info'):
            label = item.get(field)
            if isinstance(label, dict):
                label = label.get('text') or label.get('title')
            if marker(label) in AD_LABELS:
                return True
        for field in ('title', 'excerpt'):
            if DISCLOSURE.match(plain(item.get(field), 220)):
                return True
        if commercial_pitch(item):
            return True
    return False


def parse_feed_candidate(item, parser):
    if is_promotion(item):
        return {'promotion_filtered': True}
    return parser(item)
