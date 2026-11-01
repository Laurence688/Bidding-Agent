from app.domain.errors import DomainError
from app.domain.identifiers import content_hash
from app.workflows.prompts import ALLOWED, prompt_snapshot


PROMPT_ITEMS = (
    ('translate', '辅助翻译', '将原始条款翻译为中文，保留数字、条件与歧义。'),
    ('split_claims', '要求拆分', '拆分完整要求，保留原文位置和 OR 等逻辑关系。'),
    ('judge_claim', '逐项判定', '仅依据准入证据判断要求是否得到支持。'),
    ('verify_semantic_support', '语义核验', '独立核对证据、判定和最终英文口径。'),
    ('render_response', '英文口径', '依据核验结果生成正式英文应答，明确 PC 缺口。'),
    ('product_qa', '产品问答', '依据产品证据回答问题并提供引用。'),
    ('skill', '共享规则', '追加到全部模型节点的共同要求。'),
)
PROMPT_IDS = {item[0] for item in PROMPT_ITEMS}


class PromptService:
    """源码提供默认值；用户修改整包入库，运行只接收不含编辑元数据的快照。"""

    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings
        self.record_id = f'prompt-settings:{repo.local_user_id}'

    def _state(self):
        base = prompt_snapshot(self.settings.root)
        defaults = {'skill': base['skill']}
        for node in ALLOWED:
            header, separator, body = base['templates'][node].partition('\n')
            if header != f'NODE: {node}' or not separator:
                raise DomainError('PROMPT_DEFAULT_INVALID', '默认 Prompt 节点标记不正确', 500)
            defaults[node] = body
        try:
            record = self.repo.get('prompt_config', self.record_id)
        except DomainError as exc:
            if exc.code != 'NOT_FOUND':
                raise
            record = None
        prompts = record['prompts'] if record else defaults
        return record, defaults, prompts, self._bundle(base, prompts)

    @staticmethod
    def _bundle(base, prompts):
        # NODE 标记、变量白名单与 JSON 渲染契约固定由服务端提供。
        return {'templates': {node: f'NODE: {node}\n{prompts[node]}' for node in ALLOWED},
                'skill': prompts['skill'], 'variable_allowlist': base['variable_allowlist'],
                'renderer_version': base['renderer_version'], 'response_contract_version':base.get('response_contract_version','concise-v1')}

    @staticmethod
    def _view(record, defaults, prompts, bundle):
        return {'version': record['version'] if record else 0,
                'content_hash': content_hash(bundle), 'updated_at': record['updated_at'] if record else None,
                'items': [{'id': ident, 'title': title, 'description': description,
                           'content': prompts[ident], 'default_content': defaults[ident],
                           'variables': sorted(ALLOWED.get(ident, set())),
                           'is_default': prompts[ident] == defaults[ident]}
                          for ident, title, description in PROMPT_ITEMS]}

    def get(self):
        return self._view(*self._state())

    def snapshot(self):
        return self._state()[3]

    def save(self, expected_version, prompts):
        if set(prompts) != PROMPT_IDS:
            raise DomainError('PROMPT_INVALID', '请完整提交六个节点 Prompt 与共享规则，不接受其他字段', 422)
        for ident, content in prompts.items():
            if not isinstance(content, str) or not content.strip() or len(content) > 20000:
                raise DomainError('PROMPT_INVALID', '每项 Prompt 必须为 1 至 20000 字符且不能全为空白', 422,
                                  {'prompt_id': ident})
        with self.repo.transaction():
            record, defaults, current, old_bundle = self._state()
            version = record['version'] if record else 0
            # 先检查版本，即使内容相同也不能吞掉其他页面的并发更新。
            if expected_version != version:
                raise DomainError('VERSION_CONFLICT', 'Prompt 已更新，请重新加载最新版本后保存', 409)
            if prompts == current:
                return self._view(record, defaults, current, old_bundle)
            bundle = self._bundle(old_bundle, prompts)
            digest = content_hash(bundle)
            payload = {'prompts': dict(prompts), 'content_hash': digest}
            if record:
                record = self.repo.update('prompt_config', self.record_id, payload, expected_version)
            else:
                record = self.repo.put('prompt_config', payload, record_id=self.record_id)
            changed = [ident for ident in PROMPT_IDS if current[ident] != prompts[ident]]
            revision = self.repo.put('prompt_revision', {**payload, 'prompt_version': record['version'],
                'previous_content_hash': content_hash(old_bundle), 'changed_ids': sorted(changed)}, parent_id=self.record_id)
            self.repo.audit(None, 'prompts.updated', {'prompt_version': record['version'],
                'revision_id': revision['id'], 'content_hash': digest, 'changed_ids': sorted(changed)})
            return self._view(record, defaults, prompts, bundle)
