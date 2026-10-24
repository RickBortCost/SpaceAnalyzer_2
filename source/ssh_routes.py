from flask import Blueprint, jsonify, request
def blueprint(service,token):
    api=Blueprint('ssh',__name__,url_prefix='/api/ssh')
    @api.before_request
    def protect():
        if request.headers.get('X-OPCUA-Token')!=token:return jsonify(error='Pagina scaduta: ricaricare la pagina SSH'),403
        if request.content_length and request.content_length>512*1024:return jsonify(error='Richiesta SSH: massimo 512 KiB'),413
    def body():
        value=request.get_json() or {}
        if not isinstance(value,dict):raise ValueError('Richiesta SSH non valida')
        return value
    @api.get('/config')
    def config():
        cfg=service.config();return jsonify(ok=True,config=cfg,known_key=service.known_key(cfg),status=service.status())
    @api.post('/config')
    def save():return jsonify(ok=True,config=service.save_config(body()))
    @api.post('/connect')
    def connect():return jsonify(ok=True,status=service.connect(body()))
    @api.post('/disconnect')
    def disconnect():service.disconnect();return jsonify(ok=True,status=service.status())
    @api.post('/trust')
    def trust():service.trust(body());return jsonify(ok=True)
    @api.post('/forget-key')
    def forget():service.forget_key(body());return jsonify(ok=True)
    @api.get('/status')
    def status():return jsonify(ok=True,**service.status(request.args.get('after','0')))
    @api.post('/execute')
    def execute():return jsonify(ok=True,job=service.execute(body()))
    @api.post('/stop')
    def stop():service.stop(body().get('id'));return jsonify(ok=True)
    @api.post('/input')
    def input_():service.send_input(body());return jsonify(ok=True)
    @api.post('/clear')
    def clear():service.clear();return jsonify(ok=True)
    return api
