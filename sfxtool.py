#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sfxtool.py - inspect, unpack and rebuild Setup Factory 9 installers
(written for the TRON 2.0 "Killer App Mod" installer, ka_mod_v1x1a_setup.exe)

Pure Python 3.8+, no third-party packages.

Commands
  info      EXE                       show the layout of the installer and verify its checksums
  list      EXE                       list every file the installer installs (from the file table)
  verify    EXE                       decode the whole payload and check it against the file table
  extract   EXE OUTDIR [--no-payload] unpack everything (payload in the original project's folder layout)
  texts-extract DAT OUTDIR            split irsetup.dat into editable text blocks (Lua scripts, HTML, ...)
  texts-apply   DAT TEXTDIR OUT_DAT   put edited text blocks back into a copy of irsetup.dat
  build     EXE OUT --replace NAME=FILE [--replace ...] [--resize]
                                      write a new installer with internal files (e.g. irsetup.dat) replaced

Installer layout (offsets are for ka_mod_v1x1a_setup.exe)
  0x000000  launcher stub (a normal Windows PE; Setup Factory's "setup.exe" runtime)
  0x03BE00  overlay: 16 magic bytes E0E0 E1E1 ... E7E7, then 10 header bytes
            [u64 size][irsetup.exe, every byte XOR 0x07]          (the real installer engine)
            [u64 size][lua5.1.dll]
            [u32 count] followed by <count> "internal files":
                name[260] u32(0) u64 size u32 crc32 u32(0) data[size]      (data = LZMA "alone" stream)
                irsetup.dat (project: screens, scripts, file table, settings), images, helper exes ...
  0x317F29  payload: the installed files, one LZMA stream after the other, in the order of the file
            table inside irsetup.dat (no per-file headers; the file table holds size and destination)
"""
import argparse, csv, lzma, mmap, os, re, shutil, struct, sys, zlib

MAGIC = bytes([0xE0, 0xE0, 0xE1, 0xE1, 0xE2, 0xE2, 0xE3, 0xE3, 0xE4, 0xE4, 0xE5, 0xE5, 0xE6, 0xE6, 0xE7, 0xE7])
XOR7 = bytes(b ^ 7 for b in range(256))
NAME_LEN = 260


# ----------------------------------------------------------------------------------------------
# LZMA helpers (the installer uses the classic ".lzma" / LZMA-alone container: 13-byte header)
# ----------------------------------------------------------------------------------------------
def lzma_header(props, dict_size, usize):
    return struct.pack('<BIQ', props, dict_size, usize)


def compress_alone(data, dict_size=1 << 24, lc=3, lp=0, pb=2):
    """LZMA-alone stream with the real uncompressed size in the header (what Setup Factory writes)."""
    filt = [{'id': lzma.FILTER_LZMA1, 'preset': 9 | lzma.PRESET_EXTREME,
             'dict_size': dict_size, 'lc': lc, 'lp': lp, 'pb': pb}]
    raw = lzma.compress(data, format=lzma.FORMAT_RAW, filters=filt)
    props = (pb * 5 + lp) * 9 + lc
    return lzma_header(props, dict_size, len(data)) + raw


def decompress_alone(blob):
    d = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
    out = d.decompress(blob)
    if not d.eof:
        raise ValueError('truncated LZMA stream')
    return out


def stream_decode(buf, off, sink, chunk=1 << 20):
    """Decode one LZMA-alone stream starting at buf[off]. Calls sink(bytes) with the output.
    Returns (uncompressed_size, compressed_bytes_consumed)."""
    d = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
    pos, total = off, 0
    while not d.eof:
        c = buf[pos:pos + chunk]
        if not c:
            raise ValueError('stream at 0x%x ends unexpectedly' % off)
        out = d.decompress(c)
        pos += len(c)
        if out:
            sink(out)
            total += len(out)
    return total, (pos - off) - len(d.unused_data)


# ----------------------------------------------------------------------------------------------
# Installer container
# ----------------------------------------------------------------------------------------------
class Entry:
    def __init__(self, name, name_off, size, crc, data_off):
        self.name, self.name_off, self.size, self.crc, self.data_off = name, name_off, size, crc, data_off
        self.size_off = name_off + NAME_LEN + 4
        self.crc_off = self.size_off + 8

    def __repr__(self):
        return 'Entry(%r, size=%d, data@0x%x)' % (self.name, self.size, self.data_off)


class Installer:
    def __init__(self, path):
        self.path = path
        self._f = open(path, 'rb')
        self.buf = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
        self._parse()

    def close(self):
        self.buf.close()
        self._f.close()

    def _parse(self):
        b = self.buf
        if b[:2] != b'MZ':
            raise ValueError('not a Windows executable')
        pe = struct.unpack('<I', b[0x3C:0x40])[0]
        nsec = struct.unpack('<H', b[pe + 6:pe + 8])[0]
        optsz = struct.unpack('<H', b[pe + 20:pe + 22])[0]
        t = pe + 24 + optsz
        end = 0
        for i in range(nsec):
            rs, ro = struct.unpack('<II', b[t + i * 40 + 16:t + i * 40 + 24])
            end = max(end, ro + rs)
        self.overlay = end
        if b[end:end + 16] != MAGIC:
            raise ValueError('Setup Factory signature not found at 0x%x - not a Setup Factory 7/8/9 installer?' % end)
        p = end + 16 + 10                      # 10 header bytes
        self.exe_size = struct.unpack('<Q', b[p:p + 8])[0]
        self.exe_off = p + 8
        p = self.exe_off + self.exe_size
        self.dll_size = struct.unpack('<Q', b[p:p + 8])[0]
        self.dll_off = p + 8
        p = self.dll_off + self.dll_size
        count = struct.unpack('<I', b[p:p + 4])[0]
        p += 4
        self.entries = []
        for _ in range(count):
            name = bytes(b[p:p + NAME_LEN]).split(b'\0')[0].decode('latin1')
            _a, size, crc, _b = struct.unpack('<IQII', b[p + NAME_LEN:p + NAME_LEN + 20])
            e = Entry(name, p, size, crc, p + NAME_LEN + 20)
            self.entries.append(e)
            p = e.data_off + size
        self.payload_off = p

    def entry(self, name):
        for e in self.entries:
            if e.name.lower() == name.lower():
                return e
        raise KeyError(name)

    def read_entry(self, e):
        data = decompress_alone(self.buf[e.data_off:e.data_off + e.size])
        return data

    def launcher(self):
        return bytes(self.buf[:self.overlay])

    def irsetup_exe(self):
        return bytes(self.buf[self.exe_off:self.exe_off + self.exe_size]).translate(XOR7)

    def lua_dll(self):
        return bytes(self.buf[self.dll_off:self.dll_off + self.dll_size])


# ----------------------------------------------------------------------------------------------
# File table inside irsetup.dat
# ----------------------------------------------------------------------------------------------
def _cstr_candidates(t, end):
    """All plausible length-prefixed (MFC CString) strings that end exactly at t[end]."""
    res = []
    for L in range(0, 256):
        k = end - L - 1
        if k < 0:
            break
        if t[k] == L and all(c >= 32 or c == 9 for c in t[k + 1:end]):
            res.append((k, bytes(t[k + 1:end]).decode('latin1')))
    return res


def parse_file_table(dat):
    """Return the list of installed files in archive order:
       dict(source, name, dest, size, pos)"""
    recs = []
    for m in re.finditer(rb'\x07Archive', dat):
        pos = m.start()
        ext = _cstr_candidates(dat, pos)
        found = None
        for ek, _ext in ext[:1]:
            for fk, folder in _cstr_candidates(dat, ek):
                for nk, name in _cstr_candidates(dat, fk):
                    for pk, path in _cstr_candidates(dat, nk):
                        if path == folder + '\\' + name and name:
                            found = (path, name, folder)
                            break
                    if found: break
                if found: break
        if not found:
            continue
        a = pos + 8                                    # after 'Archive'
        size = struct.unpack('<Q', dat[a + 3:a + 11])[0]
        dest = ''
        for mm in re.finditer(rb'%[A-Za-z0-9_]+%[\x20-\x7e]*', dat[a:a + 400]):
            s = a + mm.start()
            L = dat[s - 1]
            cand = dat[s:s + L]
            if len(cand) == L and cand.startswith(b'%'):
                dest = cand.decode('latin1')
                break
        recs.append(dict(source=found[0], name=found[1], dest=dest, size=size, pos=pos))
    return recs


def dest_to_relpath(dest, name):
    """'%AppFolder%\\Movies' + 'x.bik'  ->  'AppFolder/Movies/x.bik' (safe relative path)"""
    parts = [p for p in re.split(r'[\\/]+', dest) if p]
    parts = [p.strip('%') if p.startswith('%') and p.endswith('%') else p for p in parts]
    parts = [p for p in parts if p not in ('.', '..')]
    return os.path.join(*(parts + [name]))



def source_to_relpath(source, dest, name):
    """'D:\\Killer_App_Mod_v1.1a\\Tron 2.0\\Movies\\x.bik' -> 'Tron 2.0/Movies/x.bik'
    (drive and project root folder removed, i.e. the folder layout of the original Setup Factory project)."""
    parts = [p for p in re.split(r'[\\/]+', source) if p]
    if parts and re.match(r'^[A-Za-z]:$', parts[0]):
        parts = parts[1:]
    if len(parts) > 1:
        parts = parts[1:]                       # project root folder (name contains the version)
    parts = [p for p in parts if p not in ('.', '..')]
    return os.path.join(*parts) if parts else dest_to_relpath(dest, name)

# ----------------------------------------------------------------------------------------------
# Text blocks inside irsetup.dat (Lua scripts, HTML, constants, ...)
#   MFC CString: 0xFF <u16 length> <bytes>      (strings shorter than 255 use a 1-byte length)
# ----------------------------------------------------------------------------------------------
_TEXTY = bytes([9, 10, 13] + list(range(32, 127)) + list(range(128, 255)))
_TEXTY_SET = set(_TEXTY)


def find_text_blocks(t, min_len=40):
    blocks, i, n = [], 0, len(t)
    while i < n - 3:
        if t[i] == 0xFF:
            L = struct.unpack('<H', t[i + 1:i + 3])[0]
            if min_len <= L < 0xFFFF and i + 3 + L <= n and set(t[i + 3:i + 3 + L]) <= _TEXTY_SET:
                blocks.append((i, L))
                i += 3 + L
                continue
        i += 1
    return blocks


def block_label(t, b):
    """Name of the event / item the block belongs to (the CString just before it), if any."""
    for L in range(1, 60):
        k = b - L - 1
        if k >= 0 and t[k] == L and all(32 <= c < 127 for c in t[k + 1:b]):
            return t[k + 1:b].decode('latin1')
    m = re.search(rb'[\x20-\x7e]{4,60}$', bytes(t[max(0, b - 60):b]))     # fallback: printable text right before
    return m.group(0).decode('latin1') if m else ''


def block_kind(text):
    s = text.lstrip()[:200].lower()
    if s.startswith(b'<') or b'<body' in s or b'<html' in s:
        return 'html'
    if b'--' in text[:400] or b'function ' in text or b'Dialog.' in text or b'SessionVar.' in text:
        return 'lua'
    return 'txt'


def _slug(s):
    return re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_')[:40] or 'text'


def texts_extract(dat, outdir):
    os.makedirs(outdir, exist_ok=True)
    rows = []
    for idx, (b, L) in enumerate(find_text_blocks(dat)):
        text = bytes(dat[b + 3:b + 3 + L])
        fn = '%03d_%06x_%s.%s' % (idx, b, _slug(block_label(dat, b)), block_kind(text))
        with open(os.path.join(outdir, fn), 'wb') as f:
            f.write(text)
        rows.append((fn, b, L))
    return rows


def texts_apply(dat, textdir):
    """Rebuild irsetup.dat from the original dat + the (possibly edited) files in textdir."""
    blocks = find_text_blocks(dat)
    files = {}
    for fn in os.listdir(textdir):
        m = re.match(r'^(\d{3})_([0-9a-f]{6})_', fn)
        if m:
            files[int(m.group(1))] = os.path.join(textdir, fn)
    out, pos, changed = bytearray(), 0, 0
    for idx, (b, L) in enumerate(blocks):
        path = files.get(idx)
        if path is None:
            continue
        new = open(path, 'rb').read()
        if new == dat[b + 3:b + 3 + L]:
            continue
        if len(new) >= 0xFFFF:
            raise ValueError('%s: %d bytes - blocks of 65535 bytes or more are not supported' % (path, len(new)))
        out += dat[pos:b] + b'\xff' + struct.pack('<H', len(new)) + new
        pos = b + 3 + L
        changed += 1
    out += dat[pos:]
    return bytes(out), changed


class DatEditor:
    """Small helper for scripted edits of irsetup.dat text blocks (length prefixes are kept correct)."""

    def __init__(self, dat):
        self.d = bytearray(dat)

    def bytes(self):
        return bytes(self.d)

    def block(self, pattern):
        p = bytes(self.d).find(pattern)
        if p < 0:
            raise KeyError('pattern not found: %r' % pattern[:60])
        for b in range(p, max(0, p - 120000), -1):
            if self.d[b] == 0xFF:
                L = struct.unpack('<H', self.d[b + 1:b + 3])[0]
                if b + 3 <= p < b + 3 + L:
                    return b, L
        raise KeyError('no text block around %r' % pattern[:60])

    def set_block(self, b, L, text):
        assert len(text) < 0xFFFF
        self.d[b:b + 3 + L] = b'\xff' + struct.pack('<H', len(text)) + text

    def replace(self, pattern, old, new):
        """Replace exactly one occurrence of `old` inside the block that contains `pattern`."""
        b, L = self.block(pattern)
        text = bytes(self.d[b + 3:b + 3 + L])
        if text.count(old) != 1:
            raise ValueError('expected exactly one occurrence, found %d: %r' % (text.count(old), old[:60]))
        self.set_block(b, L, text.replace(old, new))

    def replace_between(self, pattern, start, end, new=b''):
        """In the block containing `pattern`: replace text from `start` (inclusive) to `end` (exclusive)."""
        b, L = self.block(pattern)
        text = bytes(self.d[b + 3:b + 3 + L])
        s, e = text.index(start), text.index(end)
        old = text[s:e]
        self.set_block(b, L, text[:s] + new + text[e:])
        return old


# ----------------------------------------------------------------------------------------------
# Building a new installer
# ----------------------------------------------------------------------------------------------
def build_installer(inst, replacements, out_path, resize=False, log=print):
    """replacements: {entry name: new uncompressed bytes}. Returns output path.
    Default: the new compressed data must fit into the old slot (zero padded) so the file keeps its size
    and nothing after it moves. resize=True lets the entry grow/shrink and shifts the rest (untested)."""
    plan = []
    for name, data in replacements.items():
        e = inst.entry(name)
        dict_size = struct.unpack('<I', inst.buf[e.data_off + 1:e.data_off + 5])[0]
        props = inst.buf[e.data_off]
        lc = props % 9; lp = (props // 9) % 5; pb = (props // 9) // 5
        blob = compress_alone(data, dict_size, lc, lp, pb)
        if len(blob) > e.size and not resize:
            # a bit too big: try other LZMA parameter sets (any lc/lp/pb is valid, they are stored in the header)
            for lc2, lp2, pb2 in [(3, 0, 1), (3, 0, 0), (4, 0, 0), (4, 0, 1), (2, 0, 0), (0, 0, 0)]:
                cand = compress_alone(data, dict_size, lc2, lp2, pb2)
                if len(cand) < len(blob):
                    blob = cand
        crc = zlib.crc32(data) & 0xFFFFFFFF
        packed = len(blob)
        if len(blob) <= e.size:
            newsize = e.size
            blob = blob + b'\0' * (e.size - len(blob))
        elif resize:
            newsize = len(blob)
        else:
            raise ValueError('%s: compressed size %d does not fit into the original slot of %d bytes. '
                             'Shorten the content or use --resize (untested).' % (name, len(blob), e.size))
        log('  %-24s %9d -> %9d bytes compressed (slot %d)' % (name, len(data), packed, e.size))
        plan.append((e, blob, newsize, crc))
    plan.sort(key=lambda x: x[0].data_off)
    b = inst.buf
    with open(out_path, 'wb') as out:
        pos = 0
        for e, blob, newsize, crc in plan:
            out.write(b[pos:e.size_off])
            out.write(struct.pack('<Q', newsize))
            out.write(struct.pack('<I', crc))
            out.write(b[e.crc_off + 4:e.data_off])
            out.write(blob)
            pos = e.data_off + e.size
        while pos < len(b):
            out.write(b[pos:pos + (1 << 24)])
            pos += 1 << 24
    return out_path



# ----------------------------------------------------------------------------------------------
# Payload walker
#   - size 0 in the table      -> empty file (e.g. the Killer_App_Mod.REZ placeholder), no data in the archive
#   - header 5D 00 00 80 00 + u64(size) -> LZMA stream
#   - otherwise                -> stored as is (small files), exactly `size` bytes
# ----------------------------------------------------------------------------------------------
def walk_payload(inst, recs, open_target=None, progress=None):
    """Yield (index, record, kind, crc32, consumed_bytes). open_target(record) -> binary file or None."""
    b, pos = inst.buf, inst.payload_off
    for i, r in enumerate(recs):
        size = r['size']
        f = open_target(r) if open_target else None
        crc = 0
        if size == 0:
            kind, used = 'empty', 0
        else:
            head = b[pos:pos + 13]
            is_lzma = len(head) == 13 and head[0] == 0x5D and struct.unpack('<Q', head[5:13])[0] == size
            if is_lzma:
                kind = 'lzma'

                def sink(chunk):
                    nonlocal crc
                    crc = zlib.crc32(chunk, crc)
                    if f:
                        f.write(chunk)
                total, used = stream_decode(b, pos, sink)
                if total != size:
                    raise ValueError('file %d (%s): decoded %d bytes, table says %d' % (i, r['name'], total, size))
            else:
                kind, used = 'stored', size
                chunk = b[pos:pos + size]
                crc = zlib.crc32(chunk)
                if f:
                    f.write(chunk)
        if f:
            f.close()
        pos += used
        yield i, r, kind, crc & 0xFFFFFFFF, used
        if progress and (i + 1) % 250 == 0:
            progress(i + 1, len(recs))
    inst.payload_end = pos

# ----------------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------------
def cmd_info(a):
    inst = Installer(a.exe)
    print('file size            : %d' % len(inst.buf))
    print('launcher stub        : 0x000000 - 0x%06x' % inst.overlay)
    print('irsetup.exe (xor 7)  : 0x%06x  %d bytes' % (inst.exe_off, inst.exe_size))
    print('lua5.1.dll           : 0x%06x  %d bytes' % (inst.dll_off, inst.dll_size))
    print('internal files      : %d' % len(inst.entries))
    ok = True
    for e in inst.entries:
        try:
            data = inst.read_entry(e)
            good = (zlib.crc32(data) & 0xFFFFFFFF) == e.crc
        except Exception as ex:
            data, good = b'', False
        ok &= good
        print('  %-26s data@0x%06x slot %8d  unpacked %8d  crc %s' % (e.name, e.data_off, e.size, len(data), 'ok' if good else 'MISMATCH'))
    print('payload starts       : 0x%06x  (%d bytes to end of file)' % (inst.payload_off, len(inst.buf) - inst.payload_off))
    dat = inst.read_entry(inst.entry('irsetup.dat'))
    recs = parse_file_table(dat)
    print('file table           : %d files, %d bytes uncompressed' % (len(recs), sum(r['size'] for r in recs)))
    return 0 if ok else 1


def cmd_list(a):
    inst = Installer(a.exe)
    dat = inst.read_entry(inst.entry('irsetup.dat'))
    for i, r in enumerate(parse_file_table(dat)):
        print('%5d %12d  %-60s -> %s' % (i, r['size'], source_to_relpath(r['source'], r['dest'], r['name']), r['dest']))
    return 0


def cmd_extract(a):
    inst = Installer(a.exe)
    out = a.outdir
    os.makedirs(os.path.join(out, 'internal'), exist_ok=True)
    open(os.path.join(out, 'launcher_stub.exe'), 'wb').write(inst.launcher())
    open(os.path.join(out, 'irsetup.exe'), 'wb').write(inst.irsetup_exe())
    open(os.path.join(out, 'lua5.1.dll'), 'wb').write(inst.lua_dll())
    dat = None
    for e in inst.entries:
        data = inst.read_entry(e)
        if (zlib.crc32(data) & 0xFFFFFFFF) != e.crc:
            print('WARNING: CRC mismatch in %s' % e.name)
        open(os.path.join(out, 'internal', e.name), 'wb').write(data)
        if e.name.lower() == 'irsetup.dat':
            dat = data
    rows = texts_extract(dat, os.path.join(out, 'texts'))
    print('internal files: %d, text blocks (scripts/html): %d' % (len(inst.entries), len(rows)))
    recs = parse_file_table(dat)
    with open(os.path.join(out, 'file_table.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['index', 'destination_on_target_pc', 'name', 'size', 'source_on_build_machine', 'extracted_to', 'crc32'])
        if a.no_payload:
            for i, r in enumerate(recs):
                w.writerow([i, r['dest'], r['name'], r['size'], r['source'], source_to_relpath(r['source'], r['dest'], r['name']), ''])
            print('payload skipped (%d files in the table)' % len(recs))
            return 0
        root = os.path.join(out, 'payload')

        def open_target(r):
            target = os.path.join(root, source_to_relpath(r['source'], r['dest'], r['name']))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            return open(target, 'wb')
        counts = {'lzma': 0, 'stored': 0, 'empty': 0}
        for i, r, kind, crc, used in walk_payload(inst, recs, open_target,
                                                  lambda n, t: print('  %d/%d files' % (n, t))):
            counts[kind] += 1
            w.writerow([i, r['dest'], r['name'], r['size'], r['source'], source_to_relpath(r['source'], r['dest'], r['name']), '%08x' % crc])
        print('payload: %d files extracted (%s); bytes after last stream: %d'
              % (len(recs), ', '.join('%d %s' % (v, k) for k, v in counts.items()), len(inst.buf) - inst.payload_end))
    return 0


def cmd_verify(a):
    """Decode the whole payload without writing anything and check it against the file table."""
    inst = Installer(a.exe)
    dat = inst.read_entry(inst.entry('irsetup.dat'))
    recs = parse_file_table(dat)
    n = 0
    for i, r, kind, crc, used in walk_payload(inst, recs, None, lambda n_, t: print('  %d/%d files' % (n_, t))):
        n += 1
    left = len(inst.buf) - inst.payload_end
    print('%d files decoded, sizes match the file table; %d bytes left after the last stream' % (n, left))
    return 0 if left == 0 else 2


def cmd_texts_extract(a):
    dat = open(a.dat, 'rb').read()
    rows = texts_extract(dat, a.outdir)
    for fn, b, L in rows:
        print('%-60s offset 0x%06x  %6d bytes' % (fn, b, L))
    return 0


def cmd_texts_apply(a):
    dat = open(a.dat, 'rb').read()
    new, n = texts_apply(dat, a.textdir)
    open(a.out, 'wb').write(new)
    print('%d block(s) changed; %d -> %d bytes' % (n, len(dat), len(new)))
    return 0


def cmd_build(a):
    inst = Installer(a.exe)
    repl = {}
    for r in a.replace:
        name, _, path = r.partition('=')
        repl[name] = open(path, 'rb').read()
    build_installer(inst, repl, a.out, resize=a.resize)
    inst.close()
    chk = Installer(a.out)
    for name in repl:
        data = chk.read_entry(chk.entry(name))
        assert data == repl[name], name
    print('wrote %s (%d bytes); re-read check ok' % (a.out, len(chk.buf)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('info'); p.add_argument('exe'); p.set_defaults(f=cmd_info)
    p = sp.add_parser('list'); p.add_argument('exe'); p.set_defaults(f=cmd_list)
    p = sp.add_parser('verify'); p.add_argument('exe'); p.set_defaults(f=cmd_verify)
    p = sp.add_parser('extract'); p.add_argument('exe'); p.add_argument('outdir')
    p.add_argument('--no-payload', action='store_true'); p.set_defaults(f=cmd_extract)
    p = sp.add_parser('texts-extract'); p.add_argument('dat'); p.add_argument('outdir'); p.set_defaults(f=cmd_texts_extract)
    p = sp.add_parser('texts-apply'); p.add_argument('dat'); p.add_argument('textdir'); p.add_argument('out'); p.set_defaults(f=cmd_texts_apply)
    p = sp.add_parser('build'); p.add_argument('exe'); p.add_argument('out')
    p.add_argument('--replace', action='append', required=True, metavar='NAME=FILE')
    p.add_argument('--resize', action='store_true'); p.set_defaults(f=cmd_build)
    a = ap.parse_args(argv)
    return a.f(a)


if __name__ == '__main__':
    sys.exit(main())
