"""R0 allocation scheduling must preserve bytes, failure and caller GC state."""
import gc
import hashlib

import pytest

from genshin_lore import batch_m3_physical as scanner


@pytest.mark.parametrize('enabled',[True,False])
@pytest.mark.parametrize('failure',[None,ValueError,RuntimeError])
def test_scan_restores_gc_on_success_invalid_json_and_unexpected_error(tmp_path,monkeypatch,enabled,failure):
    path=tmp_path/'synthetic.json'
    raw=b'[{"source":"synthetic"}]'
    path.write_bytes(raw)
    original_parser=scanner.parse_json_physical
    def parse(value):
        assert not gc.isenabled()
        if failure:
            raise failure('synthetic parse failure')
        return original_parser(value)
    monkeypatch.setattr(scanner,'parse_json_physical',parse)
    original_state=gc.isenabled()
    try:
        (gc.enable if enabled else gc.disable)()
        if failure is RuntimeError:
            with pytest.raises(RuntimeError,match='synthetic parse failure'):
                scanner._scan_file('ExcelBinOutput/synthetic.json',path)
        else:
            file,rows=scanner._scan_file('ExcelBinOutput/synthetic.json',path)
            assert file['source_sha256']==hashlib.sha256(raw).hexdigest()
            if failure:
                assert file['parse_status']=='unparsed_container'
                assert list(rows)==[]
            else:
                assert file['parse_status']=='parsed_json'
                assert list(rows)==[{'ordinal':0,'pointer':'/#0',
                    'value_sha256':hashlib.sha256(raw[1:-1]).hexdigest()}]
        assert gc.isenabled() is enabled
    finally:
        (gc.enable if original_state else gc.disable)()
