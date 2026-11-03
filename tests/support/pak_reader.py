"""Small independent PAK reader used only for local, opt-in sample tests."""

import struct
from pathlib import Path


TYPE_NAMES = {
    0x50CAF5257D6A140B: "JOINT_HIERARCHY",
    0x349D779A792F45C1: "GEOMETRY_1",
    0xCE3ADE693131B309: "VRAM_DESC",
    0xE7254422A7A8F476: "VRAM_DESC_TABLE",
    0xA2481DA1A5D2CE2B: "TEXTURE_TABLE",
    0x36125D3CFB7F3991: "TEXTURE_DICTIONARY",
    0x4903731234F1BEA6: "PAK_LOGIN_TABLE",
    0x53DE1E1977F9CBA4: "ANIM_GROUP",
    0x791137002DB17EBB: "MATERIAL_TABLE_1",
    0x384ADF724B123839: "FOREGROUND_SECTION_2",
}


class Pak:
    def __init__(self, path):
        self.path = str(path)
        self.d = Path(path).read_bytes()
        self.parse_header()

    def u8(self, offset):
        return self.d[offset]

    def u16(self, offset):
        return struct.unpack_from("<H", self.d, offset)[0]

    def u32(self, offset):
        return struct.unpack_from("<I", self.d, offset)[0]

    def u64(self, offset):
        return struct.unpack_from("<Q", self.d, offset)[0]

    def i64(self, offset):
        return struct.unpack_from("<q", self.d, offset)[0]

    def cstr(self, offset):
        if not 0 < offset < len(self.d):
            return ""
        end = self.d.find(b"\0", offset)
        return self.d[offset:end if end >= 0 else len(self.d)].decode("utf-8", "replace")

    def parse_header(self):
        self.magic = self.u32(0)
        if self.magic not in (2681, 2685, 68217, 68221, 2147486329):
            raise ValueError("unsupported PAK magic")
        self.isTP1 = self.magic in (2685, 68221)
        count = self.u32(0x10)
        table = self.u32(0x14)
        self.pages = [struct.unpack_from("<III", self.d, table + 12 * index)
                      for index in range(count)]
        fixups = self.u32(0x1C)
        table = self.u32(fixups + 4)
        self.fixups = {}
        for index in range(self.u32(fixups + 8)):
            record = table + 8 * index
            source_page, target_page, offset = struct.unpack_from("<HHI", self.d, record)
            self.fixups[self.pages[source_page][0] + offset] = (target_page, record)
        start = self.pages[self.u32(8)][0] + self.u32(0xC)
        self.isTLOU2 = self.u32(start + 32) == 74565
        self.padSz = 48 if self.isTP1 or self.isTLOU2 else 32
        count = self.u32(start + self.padSz)
        self.login = [struct.unpack_from("<II", self.d, start + self.padSz + 8 + 8 * index)
                      for index in range(count)]

    def ptr(self, address):
        offset = self.i64(address)
        fixup = self.fixups.get(address)
        if offset < 0 or fixup is None:
            return None
        return self.pages[fixup[0]][0] + offset

    def items(self):
        result = []
        for page, offset in self.login:
            start = self.pages[page][0]
            identifier = self.u64(start + offset + 32)
            result.append((start, offset, identifier, TYPE_NAMES.get(identifier)))
        return result
