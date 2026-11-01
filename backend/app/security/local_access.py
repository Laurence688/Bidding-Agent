from pathlib import Path, PureWindowsPath
import secrets
from app.domain.errors import DomainError


class LocalAccess:
    def __init__(self, token, origins):
        self.token, self.origins = token, set(origins)

    def check(self, origin, token):
        if origin is not None and origin not in self.origins:
            raise DomainError('ORIGIN_DENIED', '请求来源不允许', 403)
        if not token or not secrets.compare_digest(token, self.token):
            raise DomainError('LOCAL_TOKEN_REQUIRED', '需要本机启动会话令牌', 403)


def controlled_path(root, relative):
    root = Path(root).resolve()
    if '://' in str(relative) or PureWindowsPath(relative).drive or Path(relative).is_absolute():
        raise DomainError('PATH_DENIED', '仅允许受控文件标识', 400)
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or target == root:
        raise DomainError('PATH_DENIED', '文件超出授权目录', 400)
    return target
