import shutil
import tempfile
from pathlib import Path

from scripts.alltokenmon.schema import AdapterStatus


FIXTURES = Path(__file__).parent / "fixtures"


def parse_fixture(parser, runtime, relative_path):
    fixture_runtime = runtime.replace("-", "_")
    fixture = FIXTURES / fixture_runtime / Path(relative_path).name
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(fixture), str(path))
        result = parser((path, path))
    assert result.status == AdapterStatus.OK
    assert len(result.records) >= 1
    assert all(record.runtime == runtime for record in result.records)
    assert "SENTINEL_PRIVATE" not in repr(result)
    return result


def assert_status_isolation(parser, suffix):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        malformed = root / ("malformed" + suffix)
        unsupported = root / ("unsupported" + suffix)
        malformed.write_text("{bad json\n", encoding="utf-8")
        unsupported.write_text("{}\n", encoding="utf-8")
        malformed_result = parser((malformed,))
        unsupported_result = parser((unsupported,))
    assert malformed_result.status in (
        AdapterStatus.PARTIAL,
        AdapterStatus.UNSUPPORTED_FORMAT,
    )
    assert unsupported_result.status in (
        AdapterStatus.NO_DATA,
        AdapterStatus.UNSUPPORTED_FORMAT,
    )
    assert "bad json" not in repr(malformed_result)
