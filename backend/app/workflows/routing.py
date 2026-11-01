import re
from app.services.product_catalog import product_names


def product_name_pattern(name):
    return r'(?<![A-Za-z0-9-])' + re.escape(name) + r'(?![A-Za-z0-9-])'


def route_intent(text, explicit=None):
    if explicit in {'PRODUCT_QA', 'SOC_SINGLE', 'SOC_BATCH', 'EVALUATION'}:
        return explicit
    if re.search(r'\bSOC\b|合规应答|符合性应答', text, re.I):
        return 'SOC_SINGLE'
    if re.search(r'支持|是什么|是否|如何|什么|\?$', text):
        return 'PRODUCT_QA'
    return None


def product_conflicts(text, product_id, products):
    mentioned = set()
    recognized_spans = []
    for product in products:
        for name in product_names(product):
            for match in re.finditer(product_name_pattern(name), text, re.I):
                mentioned.add(product['id'])
                recognized_spans.append(match.span())
    # 系列名称不能自动获得具体机框子型号的能力或参数。
    mentioned.update(match.group().upper() for match in re.finditer(r'\bM6000-\d+[A-Za-z]*\b', text, re.I)
                     if not any(start <= match.start() and match.end() <= end for start, end in recognized_spans))
    return sorted(mentioned - {product_id})


def untrusted_instruction(text):
    return bool(re.search(r'ignore (all |the )?(previous|system) instructions|system prompt|忽略.{0,8}(指令|规则)|绕过.{0,8}(审核|导出|安全)|reveal.{0,15}(secret|api.key)', text, re.I))
