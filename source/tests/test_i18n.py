"""Language persistence updates only UI preferences, including old profiles."""
import json
import unittest
from test_integration import client as app

class Languages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.initialize();cls.http=app.app.test_client();cls.headers={'X-OPCUA-Token':app.SESSION_TOKEN}
    def post(self,data):return self.http.post('/api/ui/language',json=data,headers=self.headers)
    def test_all_languages_persist_without_changing_source_or_workspace_data(self):
        cfg=app.validated_config({'mqtt_host':'192.0.2.7','source':'opcua'})
        app.save_config(cfg)
        app.atomic_json(app.WORKSPACE_FILE,{'schema':1,'config':cfg,'selected_nodes':[{'nodeid':'mqtt:plant/data#/Nome','display_name':'Impostazioni'}],'live':{'charts':[{'name':'Nome','signalSettings':{'signal':{'alias':'Registrazioni'}}}]}})
        app.atomic_json(app.SETTINGS_FILE,{'config':cfg,'server_data':{'custom':'Prüfung é ñ'}})
        original=app.load_workspace();epoch=app.runtime.buffer_epoch
        for code in ('en','it','de','fr','es'):
            response=self.post({'ui_language':code});self.assertEqual(response.status_code,200,response.json)
            self.assertEqual(app.load_config()['ui_language'],code)
            for path in (app.WORKSPACE_FILE,app.SETTINGS_FILE):self.assertEqual(json.loads(path.read_text())['config']['ui_language'],code)
            saved=app.load_workspace();self.assertEqual(saved['live'],original['live']);self.assertEqual(saved['selected_nodes'],original['selected_nodes'])
            self.assertEqual(saved['config']['mqtt_host'],'192.0.2.7');self.assertEqual(app.runtime.buffer_epoch,epoch)
    def test_invalid_or_unauthenticated_changes_are_rejected(self):
        self.post({'ui_language':'de'})
        for body in ({'ui_language':'xx'},{'ui_language':'<script>'},{'ui_language':None},['fr'],{'ui_language':['fr']}):
            response=self.post(body);self.assertEqual(response.status_code,400,response.json)
            self.assertEqual(app.load_config()['ui_language'],'de')
        self.assertEqual(self.http.post('/api/ui/language',json={'ui_language':'fr'}).status_code,403)
    def test_old_configuration_defaults_to_italian_and_page_uses_saved_language(self):
        app.atomic_json(app.CONFIG_FILE,{'source':'opcua'})
        self.assertEqual(app.load_config()['ui_language'],'it')
        self.post({'ui_language':'es'})
        html=self.http.get('/').get_data(as_text=True)
        self.assertIn('lang="es"',html);self.assertIn('content="es"',html)

    def test_late_autosave_cannot_restore_an_older_language(self):
        old=app.validated_config({'source':'opcua','ui_language':'it'})
        self.post({'ui_language':'fr'})
        payloads=[('/api/config',old),('/api/settings/save',{'config':old,'selected_nodes':[]}),
                  ('/api/workspace',{'schema':1,'config':old,'selected_nodes':[],'live':{},'history':{}})]
        for url,body in payloads:
            result=self.http.post(url,json=body,headers=self.headers)
            self.assertEqual(result.status_code,200,result.json)
            self.assertEqual(app.load_config()['ui_language'],'fr')
        self.assertEqual(app.load_workspace()['config']['ui_language'],'fr')
        self.assertEqual(json.loads(app.SETTINGS_FILE.read_text())['config']['ui_language'],'fr')

if __name__=='__main__':unittest.main()
