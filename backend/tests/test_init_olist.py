import sys
from types import SimpleNamespace


sys.modules.setdefault("clickhouse_connect", SimpleNamespace(get_client=None))
sys.modules.setdefault("kagglehub", SimpleNamespace(dataset_download=None))

from scripts import init_olist


class FakeClickHouseClient:
    def __init__(self):
        self.commands = []
        self.inserts = []

    def command(self, statement: str):
        self.commands.append(statement)

    def raw_insert(self, table, column_names=None, insert_block=None, settings=None, fmt=None):
        assert column_names is None
        assert insert_block is not None
        sample = insert_block.read(16)
        assert sample
        self.inserts.append((table, fmt, settings))


def test_init_olist_imports_local_csv_files_with_supported_raw_insert(tmp_path, monkeypatch):
    for filename in init_olist.FILES.values():
        (tmp_path / filename).write_text("id\n1\n", encoding="utf-8")

    client = FakeClickHouseClient()
    monkeypatch.setattr(init_olist, "locate_dataset", lambda: tmp_path)
    monkeypatch.setattr(init_olist.clickhouse_connect, "get_client", lambda **_: client)

    init_olist.main()

    inserted_tables = [table for table, _, _ in client.inserts]
    assert inserted_tables == [f"raw_olist.{table}" for table in init_olist.FILES]
    assert all(fmt == "CSVWithNames" for _, fmt, _ in client.inserts)
    assert all(settings["input_format_csv_empty_as_default"] == 1 for _, _, settings in client.inserts)
