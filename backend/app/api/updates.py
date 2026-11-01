from fastapi import Body
from app.domain.errors import DomainError
from app.services.updates import UpdateService,UpdateServiceError


def install_update_routes(app,settings):
    service=UpdateService(settings.root,settings.data_dir)
    app.state.updates=service
    def call(action):
        try:return action()
        except UpdateServiceError as exc:raise DomainError(exc.code,exc.message,409) from exc

    @app.get('/api/v1/updates')
    def status():return service.status()

    @app.post('/api/v1/updates/check')
    def check(body:dict=Body(...)):
        if set(body)!={'allow_download'} or body['allow_download'] is not True:raise DomainError('UPDATE_CONFIRM_REQUIRED','请确认连接GitHub检查并自动下载更新',422)
        return call(lambda:service.check())

    @app.post('/api/v1/updates/pause')
    def pause():return service.pause()

    @app.post('/api/v1/updates/install')
    def install(body:dict=Body(...)):
        if set(body)!={'confirm_restart'}:raise DomainError('UPDATE_CONFIRM_REQUIRED','请确认自动备份、安装并重启',422)
        return call(lambda:service.prepare_install(body['confirm_restart']))
