#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_ka_fixes.py - turns the original ka_mod_v1x1a_setup.exe into the fixed ka_mod_v1x1b_setup.exe

    python apply_ka_fixes.py ka_mod_v1x1a_setup.exe ka_mod_v1x1b_setup.exe [--diff-dir changes]

All edits are made to the Setup Factory project data (irsetup.dat) inside the installer; every edit is
described in README.md. The Lua code that was added lives in fixes/version_check.lua and fixes/free_space.lua.
"""
import argparse, difflib, hashlib, os, re, sys

import sfxtool as sfx

HERE = os.path.dirname(os.path.abspath(__file__))
NEW_SITE = b'https://tron.hirnschwund.net/patches/'
CRLF = b'\r\n'


def read_lua(name):
    """Read a Lua file from fixes/ and return it with CRLF line ends (what the installer scripts use)."""
    raw = open(os.path.join(HERE, 'fixes', name), 'rb').read()
    return raw.replace(CRLF, b'\n').replace(b'\n', CRLF)


def apply_fixes(dat, log=print):
    ed = sfx.DatEditor(dat)

    # ------------------------------------------------------------------------------------------
    # 1. Version check at startup  (script: "On Preload" of the first screen)
    #    old: downloads http://www.ldso.net/tronfaq/latestversions/ka.txt; when that fails it shows
    #         a dialog, when the text lacks the version it sends you to the website and quits
    #         (the old server no longer answers properly, so it always reported a "newer version")
    #    new: tries https://tron.hirnschwund.net/patches/latestversion.txt (built-in downloader,
    #         curl.exe, PowerShell/TLS 1.2); continues silently when nothing could be fetched
    # ------------------------------------------------------------------------------------------
    old = ed.replace_between(
        b'-- Check to see what the latest version of KA is',
        b'-- Check to see what the latest version of KA is',
        b'-- Check to see if TRON 2.0, Steam, or the Mod Manager is running',
        read_lua('version_check.lua'))
    assert b'ldso.net/tronfaq/latestversions/ka.txt' in old
    log('1. version check replaced')

    # ------------------------------------------------------------------------------------------
    # 2. Steam "run as administrator"  (script: "On Preload" of the finish screen, the big one)
    #    removes the registry write that put "~ RUNASADMIN" on Steam.exe, plus the dialog that
    #    told the user about it. (RUNASADMIN for TRON.exe / Lithtech.exe / TRONSrv.exe is untouched.)
    # ------------------------------------------------------------------------------------------
    reg_start = b'\t-- Set Steam.exe to RUNASADMIN in AppCompatFlags, as a precaution'
    old = ed.replace_between(reg_start, reg_start, b'\t-- Get the TRON 2.0 install path')
    assert old.count(b'RUNASADMIN') == 3 and b'SteamExe' in old
    warn_start = b'-- Warn the user about setting Steam.exe to RUNASADMIN'
    old = ed.replace_between(warn_start, warn_start, b'-- Force the user to read the readme')
    assert b'Steam set to run as Admin' in old
    log('2. Steam admin code removed')

    # ------------------------------------------------------------------------------------------
    # 3. Links "...all-tron-20-files-in-one-place.html#killerappmod" / "#unofficialpatch"
    #    -> https://tron.hirnschwund.net/patches/   (other links are left alone)
    # ------------------------------------------------------------------------------------------
    url_re = re.compile(rb'https?://tronfaq\.blogspot\.com/2006/02/all-tron-20-files-in-one-place\.html'
                        rb'#(?:killerappmod|unofficialpatch)')
    n = 0
    while True:
        m = url_re.search(ed.bytes())
        if not m:
            break
        b, L = ed.block(m.group(0))
        text, k = url_re.subn(NEW_SITE, bytes(ed.d[b + 3:b + 3 + L]))
        ed.set_block(b, L, text)
        n += k
    log('3. %d link(s) rewritten' % n)

    # ------------------------------------------------------------------------------------------
    # 4. No URL shortcuts on the desktop ("LDSO Site", "Additional TRON 2.0 File Downloads");
    #    the Start-menu entries and the program shortcut on the desktop stay
    # ------------------------------------------------------------------------------------------
    desk = re.compile(rb'Shell\.CreateShortcut\(Shell\.GetFolder\(SHF_DESKTOP_COMMON\), '
                      rb'"(?:LDSO Site|Additional TRON 2\.0 File Downloads)", "http[^\r\n]*\r\n')
    b, L = ed.block(b'Shell.CreateShortcut(Shell.GetFolder(SHF_DESKTOP_COMMON), "LDSO Site"')
    text, k = desk.subn(b'', bytes(ed.d[b + 3:b + 3 + L]))
    assert k == 2
    text = text.replace(b'-- Create shortcuts for the LDSO site and All TRON 2.0 Files Page\r\n',
                        b'-- Create shortcuts for the LDSO site and All TRON 2.0 Files Page '
                        b'(Start menu only; no URL shortcuts on the desktop)\r\n', 1)
    ed.set_block(b, L, text)
    log('4. %d desktop URL shortcuts removed' % k)

    # ------------------------------------------------------------------------------------------
    # 5. "Not enough free space" with 2 TB or more free: Drive.GetFreeSpace() returns whole MB as a
    #    signed 32-bit number which overflows at 2 TB. Three places:
    #    a) global function g_GetFreeSpaceInBytes() (Global Functions) -> exact value via PowerShell
    #    b) startup check in "On Preload" of the first screen uses that function
    #    c) "Select Install Folder" screen shows >= 2 TB itself, in TB
    # ------------------------------------------------------------------------------------------
    old_fn = (b'function g_GetFreeSpaceInBytes(strDrive)\r\n'
              b'\tlocal nFreeSpace = Drive.GetFreeSpace(strDrive);\r\n'
              b'\tif(nFreeSpace ~= -1) then\r\n'
              b'\t\t-- convert from MB to bytes\r\n'
              b'\t\tnFreeSpace = nFreeSpace * 1024 * 1024;\r\n'
              b'\telse\r\n'
              b'\t\tnFreeSpace = 0;\r\n'
              b'\tend\r\n'
              b'\treturn nFreeSpace;\r\n'
              b'end')
    ed.replace(b'function g_GetFreeSpaceInBytes', old_fn, read_lua('free_space.lua').rstrip(CRLF))
    ed.replace(b'hdFree = Drive.GetFreeSpace(strRegValue);',
               b'hdFree = Drive.GetFreeSpace(strRegValue);',
               b'hdFree = g_GetFreeSpaceInBytes(strRegValue) / 1048576;')
    ed.replace(b'String.GetFormattedSize(_SpaceAvailable)',
               b'SessionVar.Set("%SpaceAvailable%", String.GetFormattedSize(_SpaceAvailable) );',
               b'if(_SpaceAvailable >= 2199023255552) then\r\n'
               b'\t\t\t-- 2 TB or more\r\n'
               b'\t\t\tSessionVar.Set("%SpaceAvailable%", Math.Round(_SpaceAvailable / 1099511627776, 2).." TB");\r\n'
               b'\t\telse\r\n'
               b'\t\t\tSessionVar.Set("%SpaceAvailable%", String.GetFormattedSize(_SpaceAvailable) );\r\n'
               b'\t\tend')
    log('5. free-space fix applied')

    # ------------------------------------------------------------------------------------------
    # 6. Version 1.1a -> 1.1b everywhere in the project data (same length, so nothing shifts).
    #    The build-machine source paths "D:\Killer_App_Mod_v1.1a\..." of the file table are kept.
    # ------------------------------------------------------------------------------------------
    data = ed.bytes()
    out, n = bytearray(data), 0
    for m in re.finditer(rb'1\.1a', data):
        if data[max(0, m.start() - 16):m.start()].endswith(b'Killer_App_Mod_v'):
            continue
        out[m.start():m.end()] = b'1.1b'
        n += 1
    ed.d = out
    log('6. %d version string(s) changed to 1.1b' % n)

    # ------------------------------------------------------------------------------------------
    # 7. Readme shown in the installer (HTML, one copy per language: English, German, French,
    #    Spanish, Italian): new date and a note at the top
    # ------------------------------------------------------------------------------------------
    note = (b'<font color="#aa0000"><b>NOTE: This installer (v1.1b) is an unofficial fix of the original '
            b'v1.1a installer. The fix was autogenerated with AI. It fixes the online version check '
            b'(which always reported a newer version and blocked the installation) and the problem that '
            b'Steam was set to run as Administrator. It also contains a potential fix for the false '
            b'"not enough free space" error on drives with more than 2 TB free (this fix is untested).'
            b'<br><br>If you want the original installer, you can get it at '
            b'<a href="https://tron.hirnschwund.net/">https://tron.hirnschwund.net/</a></b></font><br><br>\r\n')
    old_hdr = b'<b>TRON 2.0: Killer App Mod by LDSO and Killer App Mod Team<br>\r\nv1.1b 10/30/14</b>'
    new_hdr = note + b'<b>TRON 2.0: Killer App Mod by LDSO and Killer App Mod Team<br>\r\nv1.1b 11/03/26</b>'
    for _ in range(5):
        ed.replace(b'v1.1b 10/30/14', old_hdr, new_hdr)
    log('7. readme updated (5 language copies)')

    new = ed.bytes()
    assert b'String.Find(strKAValue, "1.1b"' in new          # version check compares with 1.1b
    assert new.count(b'Steam.exe to RUNASADMIN') == 0
    assert b'RUNASADMIN' in new                               # the game exes keep theirs
    assert new.count(b'10/30/14') == 0
    return new


# block index (see `sfxtool.py texts-extract`) -> readable name, for the original ka_mod_v1x1a installer
FRIENDLY = {1: 'Global_Functions', 2: 'Welcome_to_Setup_screen_On_Preload', 8: 'Readme_HTML_all_5_languages',
            33: 'Select_Install_Folder_screen_On_Ctrl_Message', 47: 'Finished_Install_screen_On_Preload',
            48: 'Finish_text_English', 49: 'Finish_text_German', 50: 'Finish_text_French',
            51: 'Finish_text_Spanish', 52: 'Finish_text_Italian'}


def write_diffs(old_dat, new_dat, outdir):
    """One unified diff per changed text block (identical diffs, e.g. the 5 readme copies, are written once)."""
    os.makedirs(outdir, exist_ok=True)
    b0, b1 = sfx.find_text_blocks(old_dat), sfx.find_text_blocks(new_dat)
    assert len(b0) == len(b1)
    seen, written = {}, []
    for i, ((a, La), (b, Lb)) in enumerate(zip(b0, b1)):
        t0 = old_dat[a + 3:a + 3 + La].decode('latin1').split('\r\n')
        t1 = new_dat[b + 3:b + 3 + Lb].decode('latin1').split('\r\n')
        if t0 == t1:
            continue
        diff = '\n'.join(difflib.unified_diff(t0, t1, 'original block %03d' % i, 'fixed block %03d' % i, lineterm='', n=2))
        key = hashlib.md5(diff.split('\n', 2)[2].encode('latin1')).hexdigest()
        if key in seen:
            seen[key][1].append(i)
            continue
        seen[key] = (diff, [i], sfx.block_label(old_dat, a))
    for key, (diff, idxs, label) in seen.items():
        friendly = FRIENDLY.get(idxs[0]) or re.sub(r'[^A-Za-z0-9]+', '_', label).strip('_') or 'block'
        name = '%03d_%s.diff' % (idxs[0], friendly)
        hdr = '# text block(s) %s of irsetup.dat (extract them with: sfxtool.py texts-extract)\n' % ', '.join('%03d' % x for x in idxs)
        open(os.path.join(outdir, name), 'w', encoding='latin1', newline='\n').write(hdr + diff + '\n')
        written.append(name)
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input', help='original ka_mod_v1x1a_setup.exe')
    ap.add_argument('output', help='fixed installer to write')
    ap.add_argument('--diff-dir', help='also write readable before/after diffs of every changed script here')
    a = ap.parse_args()

    inst = sfx.Installer(a.input)
    old_dat = inst.read_entry(inst.entry('irsetup.dat'))
    new_dat = apply_fixes(old_dat)
    if a.diff_dir:
        for n in write_diffs(old_dat, new_dat, a.diff_dir):
            print('   diff:', n)
    print('building installer...')
    sfx.build_installer(inst, {'irsetup.dat': new_dat}, a.output)
    inst.close()
    chk = sfx.Installer(a.output)
    assert chk.read_entry(chk.entry('irsetup.dat')) == new_dat
    print('done: %s (%d bytes)' % (a.output, len(chk.buf)))


if __name__ == '__main__':
    sys.exit(main())
