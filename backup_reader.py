import re
import zlib
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class TocEntry:
    dump_id: int
    tag: str
    desc: str
    defn: str
    copy_stmt: str
    namespace: str
    offset_flag: int
    offset: int
    deps: list = field(default_factory=list)


class BackupReader:
    def __init__(self, path):
        self.path = str(path)
        self.f = open(self.path, "rb")
        self._read_header()
        self._read_toc()

    # низкоуровневое чтение
    def _byte(self):
        b = self.f.read(1)
        if not b:
            raise EOFError
        return b[0]

    def _int(self):
        sign = self._byte()
        val = 0
        for i in range(self.int_size):
            val |= self._byte() << (8 * i)
        return -val if sign else val

    def _str(self):
        n = self._int()
        if n < 0:
            return None
        return self.f.read(n).decode("utf-8", errors="replace")

    def _offset(self):
        flag = self._byte()
        off = 0
        for i in range(self.off_size):
            off |= self._byte() << (8 * i)
        return flag, off

    # заголовок и оглавление
    def _read_header(self):
        if self.f.read(5) != b"PGDMP":
            raise ValueError("Это не custom-формат pg_dump (нет сигнатуры PGDMP)")
        self.vmaj, self.vmin, self.vrev = self._byte(), self._byte(), self._byte()
        self.version = (self.vmaj, self.vmin, self.vrev)
        self.int_size = self._byte()
        self.off_size = self._byte()
        self.format = self._byte()
        if self.version >= (1, 15, 0):
            self.compression = self._byte()  # 0 none, 1 gzip, 2 lz4, 3 zstd
        else:
            level = self._int()
            self.compression = 0 if level == 0 else 1
        for _ in range(7):  
            self._int()
        self.dbname = self._str()
        self.server_version = self._str()
        self.pgdump_version = self._str()

    def _read_toc(self):
        n = self._int()
        self.toc = []
        for _ in range(n):
            dump_id = self._int()
            self._int()  # had_dumper
            self._str(); self._str()  # tableoid, oid
            tag = self._str(); desc = self._str()
            self._int()  # section
            defn = self._str(); self._str()  # defn, drop
            copy_stmt = self._str(); namespace = self._str(); self._str()  
            if self.version >= (1, 14, 0):
                self._str()  # tableam
            if self.version >= (1, 16, 0):
                self._int()  # relkind
            self._str()  # owner
            self._str()  # with_oids
            deps = []
            while True:
                d = self._str()
                if d is None:
                    break
                deps.append(d)
            flag, off = self._offset()
            self.toc.append(TocEntry(dump_id, tag, desc, defn, copy_stmt, namespace, flag, off, deps))
        self._by_table = {e.tag: e for e in self.toc if e.desc == "TABLE DATA"}

    # данные 
    def tables(self):
        return sorted(self._by_table)

    def _read_data_block(self, e):
        if e.offset_flag != 2:
            return b""
        self.f.seek(e.offset)
        self._byte()   # тип блока
        self._int()    # dump_id
        chunks = []
        while True:
            n = self._int()
            if n == 0:
                break
            chunks.append(self.f.read(n))
        raw = b"".join(chunks)
        if self.compression == 1:
            raw = zlib.decompress(raw)
        elif self.compression in (2, 3):
            raise NotImplementedError("lz4/zstd не поддержаны; сделайте дамп с gzip или восстановите через pg_restore")
        return raw

    @staticmethod
    def _unescape(s):
        if "\\" not in s:
            return s
        out, i = [], 0
        m = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
        while i < len(s):
            c = s[i]
            if c == "\\" and i + 1 < len(s):
                i += 1
                out.append(m.get(s[i], s[i]))
            else:
                out.append(c)
            i += 1
        return "".join(out)

    def columns(self, name):
        stmt = self._by_table[name].copy_stmt or ""
        m = re.search(r"\((.*)\)\s+FROM", stmt, re.S)
        return [c.strip().strip('"') for c in m.group(1).split(",")] if m else None

    def table(self, name):
        e = self._by_table[name]
        text = self._read_data_block(e).decode("utf-8", errors="replace")
        cols = self.columns(name)
        rows = []
        for line in text.split("\n"):
            if line == "" or line == "\\.":
                continue
            rows.append([None if v == "\\N" else self._unescape(v) for v in line.split("\t")])
        df = pd.DataFrame(rows, columns=cols)
        return df

    def load_all(self, only=None):
        return {t: self.table(t) for t in self.tables() if only is None or t in only}

    def ddl(self, name):
        for e in self.toc:
            if e.desc == "TABLE" and e.tag == name:
                return e.defn
