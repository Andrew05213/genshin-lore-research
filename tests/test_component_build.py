"""Component build tests: no game text or upstream files shipped."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('public_rebuild',ROOT/'scripts/build_component.py')
rebuild=importlib.util.module_from_spec(spec)
spec.loader.exec_module(rebuild)


class PublicRebuildTests(unittest.TestCase):
    def test_relative_output_is_absolute_at_narrative_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            def build(family,source,artifact,accounting_root):
                self.assertTrue(source.is_absolute())
                self.assertTrue(artifact.is_absolute())
                artifact.write_bytes(b'synthetic')
                return {'artifact':{'size_bytes':9,'sha256':hashlib.sha256(b'synthetic').hexdigest()}}
            def verify(source,artifact,report):
                self.assertTrue(artifact.is_absolute())
                self.assertTrue(artifact.as_uri().startswith('file:'))
                return {'verdict':'PASS'}
            fake_builder=SimpleNamespace(build=build)
            fake_verifier=SimpleNamespace(verify=verify)
            with patch('os.getcwd',return_value=temp),patch.object(rebuild,'qualify'),patch.object(rebuild.shutil,'disk_usage',return_value=SimpleNamespace(free=4_000_000_000)),patch.dict('sys.modules',{'genshin_lore.narrative_7_1_build':fake_builder,'genshin_lore.narrative_7_1_verify':fake_verifier}):
                result=rebuild.rebuild(Path('source'),'narrative_npc',Path('relative-output'))
            self.assertEqual(result['status'],'PASS')
            self.assertTrue((Path(temp)/'relative-output/narrative_npc/verify.json').exists())
            report=json.loads((Path(temp)/'relative-output/narrative_npc/verify.json').read_text())
            self.assertEqual(report['verdict'],'PASS')
            self.assertEqual(report['status'],'PASS')

    def test_narrative_failed_verdict_is_preserved_and_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            fake_builder=SimpleNamespace(build=lambda *a:{'artifact':{'size_bytes':0,'sha256':'synthetic'}})
            fake_verifier=SimpleNamespace(verify=lambda *a:{'verdict':'FAIL','reason':'synthetic rejection'})
            with patch.object(rebuild,'qualify'),patch.object(rebuild.shutil,'disk_usage',return_value=SimpleNamespace(free=4_000_000_000)),patch.dict('sys.modules',{'genshin_lore.narrative_7_1_build':fake_builder,'genshin_lore.narrative_7_1_verify':fake_verifier}):
                with self.assertRaises(ValueError):rebuild.rebuild(Path(temp),'narrative_npc',Path(temp)/'output')
            report=json.loads((Path(temp)/'output/narrative_npc/verify.json').read_text())
            self.assertEqual(report['verdict'],'FAIL')
            self.assertEqual(report['status'],'FAIL')

    def test_declared_portable_components(self):
        self.assertEqual(len(rebuild.components()),14)
        self.assertIn('character_foundation',rebuild.components())
        self.assertIn('narrative_dialog',rebuild.components())

    def test_compare_equal_and_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            dirs=[Path(temp)/arm for arm in ('a','b')]
            for directory in dirs:
                directory.mkdir()
                raw=b'synthetic-artifact'
                (directory/'artifact.sqlite').write_bytes(raw)
                report={'artifact':{'size_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}}
                (directory/'build.json').write_text(json.dumps(report))
                (directory/'verify.json').write_text('{"status":"PASS"}')
                (directory/'invocation.json').write_text('{"component":"synthetic"}')
            self.assertEqual(rebuild.compare(*dirs)['status'],'PASS')
            (dirs[1]/'artifact.sqlite').write_bytes(b'changed')
            with self.assertRaises(ValueError):rebuild.compare(*dirs)

    def test_failed_verification_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            (directory/'verify.json').write_text('{"status":"FAIL"}')
            (directory/'build.json').write_text('{}')
            with self.assertRaises(ValueError):rebuild.compare(directory,directory)


if __name__=='__main__':unittest.main()
