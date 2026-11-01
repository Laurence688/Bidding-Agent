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
        if set(body)!={'allow_check'} or body['allow_check'] is not True:raise DomainError('UPDATE_CONFIRM_REQUIRED','请确认连接GitHub检查版本；检查不会下载或安装',422)
        return call(lambda:service.check())

    @app.put('/api/v1/updates/preferences')
    def preferences(body:dict=Body(...)):
        if set(body)!={'enabled'} or type(body['enabled']) is not bool:raise DomainError('UPDATE_PREFERENCE_INVALID','请明确设置更新开关',422)
        return call(lambda:service.set_enabled(body['enabled']))

    @app.post('/api/v1/updates/download')
    def download(body:dict=Body(...)):
        if set(body)!={'allow_download'} or body['allow_download'] is not True:raise DomainError('UPDATE_CONFIRM_REQUIRED','请确认下载新版；下载完成不会自动安装',422)
        return call(lambda:service.download())

    @app.post('/api/v1/updates/pause')
    def pause():return service.pause()

    @app.post('/api/v1/updates/install')
    def install(body:dict=Body(...)):
        if set(body)!={'confirm_restart'}:raise DomainError('UPDATE_CONFIRM_REQUIRED','请确认自动备份、安装并重启',422)
        return call(lambda:service.prepare_install(body['confirm_restart']))
