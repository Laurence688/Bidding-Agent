from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid5, NAMESPACE_URL
from datetime import datetime
import json
import re
from app.domain.identifiers import utc_now, content_hash
from app.domain.errors import DomainError
from app.security.redaction import redact

ACTIVE = ContextVar('soc_local_trace', default=None)
BLOCKED = {'api_key', 'authorization', 'token', 'headers', 'reasoning_content', 'reasoning',
           'thinking', 'attachments', 'attachment', 'file_path', 'path', 'local_path', 'embedding_path'}


def sanitize(value, secrets=()):
    if isinstance(value, dict):
        return {k: sanitize(v, secrets) for k, v in value.items()
                if k.lower() not in BLOCKED and not k.lower().endswith(('_api_key', '_path'))}
    if isinstance(value, (list, tuple)):
        return [sanitize(v, secrets) for v in value]
    if isinstance(value, str):
        # Prompts contain JSON-encoded evidence; filter embedded objects too.
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(sanitize(parsed, secrets), ensure_ascii=False)
        value = redact(value, secrets)
        value = re.sub(r'(?i)(?:[a-z]:[\\/]+|\\\\)[^\s"<>，；]+', '[LOCAL_PATH]', value)
        return re.sub(r'(?<!\w)/(?:Users|home|tmp|var|mnt)/[^\s"<>]+', '[LOCAL_PATH]', value)
    return value


def destination(settings):
    config = getattr(settings, 'langsmith', {})
    return {k: config.get(k) for k in ('endpoint', 'project', 'workspace_id')}


class Span:
    def __init__(self, recorder, ident, name, inputs, run_type, metadata, parent):
        self.recorder, self.ident, self.outputs = recorder, ident, {}
        self.payload = {'id': ident, 'name': name, 'inputs': inputs, 'run_type': run_type,
                        'start_time': utc_now(), 'extra': {'metadata': metadata or {}}}
        order = datetime.fromisoformat(self.payload['start_time'].replace('Z', '+00:00')).strftime('%Y%m%dT%H%M%S%fZ') + ident
        self.payload['dotted_order'] = (parent.payload['dotted_order'] + '.' if parent else '') + order
        if parent:
            self.payload['parent_run_id'] = parent.ident
            self.payload['trace_id'] = parent.payload.get('trace_id', parent.ident)
        else:
            self.payload['trace_id'] = ident

    def finish(self, outputs):
        self.outputs = outputs
        if self.payload['run_type'] == 'llm' and outputs.get('usage_metadata'):
            self.payload['extra']['metadata']['usage_metadata'] = outputs['usage_metadata']


class NullSpan:
    def finish(self, outputs):
        pass


class Recorder:
    def __init__(self, repo, settings):
        self.repo, self.settings = repo, settings

    @contextmanager
    def span(self, key, name, inputs, run_type='chain', metadata=None):
        if not getattr(self.settings, 'langsmith', {}).get('enabled'):
            yield NullSpan()
            return
        parent = ACTIVE.get()
        ident = str(uuid5(NAMESPACE_URL, 'soc:' + str(key)))
        if parent:
            metadata = {**{k: v for k, v in parent.payload['extra']['metadata'].items() if k in {'run_id', 'mode', 'product_id', 'config_snapshot_id', 'knowledge_snapshot_id', 'evaluation_run_id', 'session_id', 'task_id', 'clause_id'}}, **(metadata or {})}
        span = Span(self, ident, name, inputs, run_type, metadata, parent)
        # Persist hierarchy at entry: process death can leave children committed
        # before the root has an outbox record. Resume must preserve its prefix.
        try:
            with self.repo.transaction():
                try:
                    header = self.repo.get('trace_span', ident)
                except DomainError as exc:
                    if exc.code != 'NOT_FOUND':
                        raise
                    header = self.repo.put('trace_span', {k: span.payload[k] for k in
                        ('start_time', 'dotted_order', 'trace_id')}, record_id=ident)
                span.payload.update({k: header[k] for k in ('start_time', 'dotted_order', 'trace_id')})
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Local trace header persistence failed')
        if parent and parent.payload.get('project_name'):
            span.payload['project_name'] = parent.payload['project_name']
        elif (metadata or {}).get('evaluation_run_id'):
            span.payload['project_name'] = 'soc-experiment-' + metadata['evaluation_run_id']
        token = ACTIVE.set(span)
        try:
            yield span
        except BaseException as exc:
            span.payload['error'] = getattr(exc, 'code', type(exc).__name__)
            raise
        finally:
            ACTIVE.reset(token)
            # Telemetry must never replace a business outcome or rerun a model.
            try:
                span.payload.update(outputs=span.outputs, end_time=utc_now())
                dependencies = []
                evaluation_id = (metadata or {}).get('evaluation_run_id')
                if evaluation_id and not parent:
                    from app.telemetry.experiments import prepare_experiment, example_id
                    dependencies = prepare_experiment(self.repo, self.settings, evaluation_id)
                    execution = self.repo.get('evaluation_case_run', metadata['run_id'])
                    evaluation = self.repo.get('evaluation_run', evaluation_id)
                    span.payload['reference_example_id'] = example_id(evaluation['dataset_version_id'], execution.get('case_id', 'legacy-' + str(execution.get('case_index'))))
                    span.payload['project_name'] = 'soc-experiment-' + evaluation_id
                elif parent:
                    if parent.payload.get('project_name'):
                        span.payload['project_name'] = parent.payload['project_name']
                self.enqueue(ident, 'trace', span.payload, (metadata or {}).get('run_id') or (parent.payload['extra']['metadata'].get('run_id') if parent else None), dependencies)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('Local telemetry persistence failed')

    def enqueue(self, key, operation, payload, run_id=None, dependencies=None):
        dest = destination(self.settings)
        ident = str(uuid5(NAMESPACE_URL, content_hash(dest) + ':' + operation + ':' + str(key)))
        secret_values = [v for k, v in self.settings.secrets.items() if v and (k.endswith('_API_KEY') or k.endswith('_TOKEN'))]
        safe = sanitize(payload, secret_values)
        with self.repo.transaction():
            try:
                return self.repo.get('cloud_outbox', ident)
            except DomainError as exc:
                if exc.code != 'NOT_FOUND':
                    raise
            return self.repo.put('cloud_outbox', {'operation': operation, 'payload': safe, 'destination': dest,
                'run_id': run_id, 'status': 'pending', 'attempts': 0, 'next_attempt': 0,
                'dependencies': dependencies or []}, record_id=ident)


@contextmanager
def child_span(key, name, inputs, run_type='chain', metadata=None):
    parent = ACTIVE.get()
    if parent is None:
        yield NullSpan()
    else:
        with parent.recorder.span(key, name, inputs, run_type, metadata) as span:
            yield span
