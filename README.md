# TRON 2.0 Killer App Mod Patcher

This mod has been built with Claude AI. It unpacks the Killer App Mod 1.1a, 
applies a few fixes (notably, a broken online check, a "Run as administrator flag"
that broke Steam and a problem if you had more than 2TB of free space) and then repacks 
it as a new version "1.1b".

THIS SHOULD ONLY BE TREATED AS AN EMERGENCY FIX AND NOT AS A SOLID BASE FOR FURTHER MOD DEVELOPMENT.
All credit for the original installer goes to LDSO. While he did good work, he has sadly been absent 
for years and did not share the source code for the installer with the community.

Ideally, with these AI-generated tools all of the relevant files and scripts can be unpacked and 
used to develop a new, more future-proof installer that does not depend on proprietary software.

Sadly, I lack the time and expertise to do this. Have fun and get cracking!

On a personal note, I know that AI is being shoved into all of our lives violently and is threatening 
the profession of software development as a whole. In this case, it has proven useful. Do know that I 
am not proud of tainting my Github profile by using it, but knowing that Anthropic probably burned thousands
of dollars to have their agents run for half an hour fixing up a really old computer game makes me
sleep a little better at night.

If I had the time, I would've done this by hand (and I would've actually been proud of it).

# Sloppy, AI-generated README continues:

This toolkit lets you take the installer `ka_mod_v1x1a_setup.exe` apart, see every script and every
installed file, re-apply the fixes made for `ka_mod_v1x1b_setup.exe`, and build a new installer
after you changed something.

| File | What it is |
|---|---|
| `sfxtool.py` | General tool: inspect / unpack / edit / rebuild this kind of installer |
| `apply_ka_fixes.py` | Re-creates `ka_mod_v1x1b_setup.exe` from the original installer, step by step |
| `fixes/version_check.lua` | The new startup version check (Lua) |
| `fixes/free_space.lua` | The new free-space code for drives with 2 TB or more (Lua) |
| `changes/*.diff` | Readable before/after diffs of every script that was changed |

Everything is plain Python 3.8 or newer, no extra packages. Nothing in here has been run on Windows by
me – it was developed and tested on Linux, and the resulting installer was never started. See
"Limits and what is untested" at the end.

---

## 1. Quick start

Install Python 3 from python.org (tick "Add python.exe to PATH"), open a Command Prompt in the folder with
these files and the original installer, then:

```
python apply_ka_fixes.py ka_mod_v1x1a_setup.exe ka_mod_v1x1b_setup.exe
```

That reproduces the fixed installer (and `--diff-dir changes` rewrites the diffs). On my machine the result has
SHA-256 `0c7127f0c4b2d96076404c5f779ef0e46eb33547cb2f798f2d93b492a00b4a2a`; the original is
`2c960e9d15dd0b69cb1bbd6b733cd6d8ef2251e3a0cd1421a556d274534c86d2`. Your result can differ in a few
compressed bytes if your Python was built with a different LZMA library version; it is still a valid file with the
same content. Check with `python sfxtool.py info ka_mod_v1x1b_setup.exe` (all CRCs must say `ok`).

If `python` is not found, try `py -3` instead.

---

## 2. What the installer is made of

It is a **Setup Factory 9** installer (the project data mentions Setup Factory 9 and version 9.3.2.0).
There is no separate "source project" inside it. What the installer contains is the *compiled* project:

| Part | Where (original file) | Notes |
|---|---|---|
| Launcher stub | `0x000000`–`0x03BE00` | An ordinary Windows program, Setup Factory's `setup.exe` runtime. Not touched. |
| Magic bytes | `0x03BE00` | `E0E0 E1E1 E2E2 … E7E7`, followed by 10 header bytes |
| `irsetup.exe` | `0x03BE22`, 1,367,040 bytes | The installer engine. Stored with every byte XOR `0x07`. |
| `lua5.1.dll` | `0x189A2A`, 328,408 bytes | Lua interpreter used for the scripts |
| 14 "internal files" | `0x1D9D02`… | Each: `name[260]`, `u32 0`, `u64 size`, `u32 crc32`, `u32 0`, then an LZMA "alone" stream |
| Payload | `0x317F29` to end of file | 1,751 files, one LZMA stream after another, in file table order |

The internal files are: **`irsetup.dat`**, the installer's images (`IRIMG1.JPG/BMP`, `IRIMG2.JPG/BMP`, `IRIMG3.JPG`,
`installericon.ico`) and helper programs (`LithRez.exe`, `ka_mod_43/169/1610_patcher.exe`, `msvcr71.dll`,
`nircmdc.exe`, `FileBinReplace.exe`).

**`irsetup.dat` is the project.** It is the serialized Setup Factory project: screens and their controls, the
language strings, the global functions, all event scripts, the settings and the **file table** (for each installed
file: build-machine source path, destination such as `%AppFolder%\Movies`, size, …). The scripts and the HTML of the
readme are stored in it as plain text, which is why they can be edited. Text blocks are stored as `0xFF`,
a 16-bit length, then the text (the usual MFC string format).

**Payload rules** (found by decoding the whole file, `sfxtool.py verify` checks them):

* The streams follow the file table order, with no per-file header and no gaps. After the last file the
  installer ends exactly, no trailing bytes.
* Size 0 in the table means "empty file, nothing stored". Example: `Killer_App_Mod.REZ` is an empty placeholder;
  the installer builds the real REZ file during installation (with `LithRez.exe`).
* Small files (26 of them, e.g. `customrez.txt`) are stored as they are, not compressed. Everything else is LZMA
  (all 1,724 compressed streams use the same settings, an 8 MB dictionary).

I found **no absolute offsets or sizes** of the archive anywhere (not in the stub, not in `irsetup.exe`, not in
`irsetup.dat`). The engine therefore seems to walk the container from the start, which is why a rebuilt file can have
a different size – but see the limits section.

---

## 3. Unpacking

```
python sfxtool.py info    ka_mod_v1x1a_setup.exe          layout + checksum check of the internal files
python sfxtool.py list    ka_mod_v1x1a_setup.exe          every installed file and its destination
python sfxtool.py verify  ka_mod_v1x1a_setup.exe          decodes the whole payload (about half a minute), writes nothing
python sfxtool.py extract ka_mod_v1x1a_setup.exe work     unpack everything (needs about 1.6 GB)
python sfxtool.py extract ka_mod_v1x1a_setup.exe work --no-payload    only the small parts (a few MB)
```

`extract` creates:

| In `work\` | Content |
|---|---|
| `internal\` | the 14 internal files, decompressed; `internal\irsetup.dat` is the project data |
| `texts\` | `irsetup.dat` split into 62 editable text blocks (Lua scripts, HTML, language strings), see section 4 |
| `payload\` | all 1,751 installed files, in the **folder layout of the original project** (`Tron 2.0\…`, `Retail\…`, `tcdg\…`, `Installer\…`, `ModManager\…`) |
| `file_table.csv` | index, destination on the user's PC (`%AppFolder%\…`), size, original build path, where it was extracted to, CRC32 |
| `irsetup.exe`, `lua5.1.dll`, `launcher_stub.exe` | the engine (XOR removed), the Lua DLL, the stub |

Why the project layout and not the destination layout: 18 destination paths are used by 54 different files with
*different* content (for example `gamep6.REZ` exists in 15 language/aspect-ratio variants, all installed to the same place,
only one of them is chosen at install time). The build-machine paths are unique.

---

## 4. Editing scripts and rebuilding the installer

```
python sfxtool.py extract ka_mod_v1x1a_setup.exe work --no-payload
```

1. **Edit** a file in `work\texts\`. The file name is `NNN_offset_<screen event>.lua` / `.html` / `.txt`.
   `NNN` is what `texts-apply` uses to match files, so do not rename or renumber them.
2. **Put the texts back** into a copy of the project data:
   ```
   python sfxtool.py texts-apply work\internal\irsetup.dat work\texts work\irsetup_new.dat
   ```
3. **Write the new installer** (the compressed project data is replaced; everything else stays byte for byte):
   ```
   python sfxtool.py build ka_mod_v1x1a_setup.exe my_installer.exe --replace irsetup.dat=work\irsetup_new.dat
   ```
4. **Check it**: `python sfxtool.py info my_installer.exe` – every line must say `crc ok`.
   `python sfxtool.py verify my_installer.exe` additionally decodes the payload.

`sfxtool.py build` can replace any of the 14 internal files this way (`--replace IRIMG2.JPG=newpicture.jpg`),
for example the installer's images, the icon, or the helper programs.

Rules when editing the text files:

* Keep **Windows line ends (CRLF)** and a single-byte encoding (ASCII / Windows-1252). Do not save as UTF-8 with
  special characters: non-ASCII characters would be stored as several bytes and break the strings. Check that your
  editor (Notepad++, VS Code) does not convert line ends on save.
* In the Lua code a backslash in a string is written twice, e.g. `"%AppFolder%\\Movies"`.
* One block may not be 65,535 bytes or more. (The largest one, the finish screen's `On Preload`, is 39,657 bytes.)
* `texts-apply` can **change the contents** of existing text blocks. It cannot add new screens, controls, events or
  files – those are binary structures in the project data that this toolkit does not edit.

You can also write your edits as a Python script: `sfxtool.DatEditor` (see `apply_ka_fixes.py`) finds a block by a
text anchor, replaces text and keeps the length prefix right. This is exactly how the fixes below are made.

### The size limit of `irsetup.dat`

The compressed project data lives in a fixed slot of **113,260 bytes**. The fixed installer uses 110,814 bytes, so there
are about **2.4 KB left** (about 2.9 KB with different LZMA settings, which `build` tries automatically when the data
does not fit). If your edits get bigger than that, `build` stops with a message. Options:

* shorten or remove something (the five readme copies and the 40 KB finish script are the big items), or
* use `--resize`: the slot grows and everything after it moves. I made this work technically (it re-reads correctly,
  and the payload stays decodable) and found no absolute offsets in the file, but **I could not test a resized
  installer on Windows.** Test it in a virtual machine before you hand it to anyone.

---

## 5. What was changed, and how

All changes are in `irsetup.dat`; the launcher, the engine, the payload and all other internal files are
byte-identical to the original. The block numbers are those of `sfxtool.py texts-extract` on the **original** installer.

| # | Script (block) | Change |
|---|---|---|
| 1 | "Welcome to Setup" screen → On Preload (002) | New online version check |
| 2 | "Finished Install" screen → On Preload (047) | Steam "run as administrator" removed |
| 3 | On Preload (002) | Three links changed to `https://tron.hirnschwund.net/patches/` |
| 4 | "Finished Install" screen → On Preload (047) | No URL shortcuts on the desktop |
| 5 | Global Functions (001), On Preload (002), "Select Install Folder" screen → On Ctrl Message (033) | Possible fix for "not enough free space" with 2 TB or more |
| 6 | several | Version 1.1a → 1.1b |
| 7 | readme in the installer (008–012) | New date and a note at the top |

The exact before/after text of every changed block is in `changes\`.

### 1. Version check (block 002, `changes\002_…diff`)

*Original:* the script downloaded `http://www.ldso.net/tronfaq/latestversions/ka.txt` with the built-in
downloader, looked for the text `1.1a` in it, and if it was not found it said "newer version available", opened the
website and quit. The old server no longer returns a usable file, so it always reported a newer version. If the
download failed it showed a dialog and carried on.

*Now* (`fixes/version_check.lua`):

* The URL is `https://tron.hirnschwund.net/patches/latestversion.txt`.
* The built-in Setup Factory downloader is tried first (port 443 for HTTPS). Setup Factory 9 is old and probably
  cannot do modern TLS, so it falls back to `curl.exe` (part of Windows 10 1803 and newer), then to Windows
  PowerShell with TLS 1.2 forced.
* A download only counts if the file is not empty, at most 200 characters, and contains no `<` (so an HTML error
  page is ignored).
* If a version could be fetched and it does **not** contain `1.1b`, the "Newer version available" dialog
  (now naming the version found) is shown, the download page opens and the installer quits, as before.
  If nothing could be fetched, the installer continues without any message.
* A short log of what happened is written to `%TEMP%\ka_versioncheck.log`.

By hand: in the On Preload of the first screen, replace everything from the line
`-- Check to see what the latest version of KA is` up to (not including)
`-- Check to see if TRON 2.0, Steam, or the Mod Manager is running` with the content of `fixes/version_check.lua`.

### 2. Steam "run as administrator" (block 047)

*Original:* when Steam was found, the script wrote `~ RUNASADMIN` for `Steam.exe` into
`HKCU\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers`, and at the end showed the dialog
"Steam set to run as Admin".

*Now:* both parts are removed: the block that starts with
`-- Set Steam.exe to RUNASADMIN in AppCompatFlags …` (reads `SteamExe`, fixes slashes, writes the registry value) and the
block `-- Warn the user about setting Steam.exe to RUNASADMIN … if steam == true then Dialog.Message(...) end`.
The variable that the first block set is set again right after, so nothing else depends on it.

*Not touched:* the `RUNASADMIN` compatibility flags for `TRON.exe`, `Lithtech.exe` and `TRONSrv.exe` (the game itself).

### 3. Links (block 002)

The three links ending in `…all-tron-20-files-in-one-place.html#killerappmod` and `#unofficialpatch` (the two dialogs
about the Unofficial Patch and the dialog for a wrong game version) now open `https://tron.hirnschwund.net/patches/`.
Not changed: the Start-menu shortcut "Additional TRON 2.0 File Downloads" (still the old plain blogspot address) and
the mentions of the LDSO site.

### 4. Desktop shortcuts (block 047)

The two `Shell.CreateShortcut(Shell.GetFolder(SHF_DESKTOP_COMMON), …)` lines for "LDSO Site" and "Additional TRON 2.0 File
Downloads" are deleted. The Start-menu versions and the desktop shortcut that starts TRON 2.0 without Steam are kept.
The uninstaller still tries to delete the old desktop shortcuts (harmless, and it cleans up after older installs).

### 5. Free space with 2 TB or more (blocks 001, 002, 033) – untested

*Cause (assumed):* Setup Factory's `Drive.GetFreeSpace()` returns whole megabytes as a signed 32-bit number, which
overflows at 2 TB. The installer then thinks the disk is full.

*Now* (`fixes/free_space.lua`):

* The global function `g_GetFreeSpaceInBytes()` (block 001): if the value from `Drive.GetFreeSpace()` is below 2.5 GB,
  it asks Windows for the exact free space through PowerShell (`[IO.DriveInfo].AvailableFreeSpace`, 64 bit), cached per
  drive. If PowerShell is not available and the value is negative (but not the error value -1), it assumes at least 2 TB.
* Block 002: the first check ("at least 2560 MB free") now calls `g_GetFreeSpaceInBytes()` instead of
  `Drive.GetFreeSpace()` directly.
* Block 033 (folder selection): free space of 2 TB or more is shown as "x.xx TB" by the script itself.

### 6. Version 1.1a → 1.1b (several places)

Fourteen texts, all the same length: the compare string in the version check, the two lines that set the server name
in `Default.txt` ("TRON 2.0: KAMod v1.1b"), the finish-screen text in five languages (blocks 048–052), `%ProductVer%` in the
project settings, and the readme (next item). The build-machine source paths of the file table
(`D:\Killer_App_Mod_v1.1a\…`) are left as they are – they are only information.

### 7. Readme (blocks 008–012, one per language)

The text at the top is now (in all five languages):

> NOTE: This installer (v1.1b) is an unofficial fix of the original v1.1a installer. The fix was autogenerated with AI.
> It fixes the online version check … and the problem that Steam was set to run as Administrator. It also contains a
> potential fix for the false "not enough free space" error on drives with more than 2 TB free (this fix is untested).
> If you want the original installer, you can get it at https://tron.hirnschwund.net/

and the date line reads `v1.1b 11/03/26`. The separate `readme.html` that the finish screen opens is a payload
file (`Retail\readme.html`) and is **not** changed.

---

## 6. Getting to "the sources" and developing further

There are three realistic ways, from small to large:

**A. Keep patching the compiled project (this toolkit).** Good for script and text changes, replacing images, icon and
helper programs. Not possible: new screens, controls, events, changed/added/removed installed files, changed file
table. Limit: the 2.4 KB of headroom (section 4).

**B. Recreate the project in Setup Factory 9.** Setup Factory 9 is a commercial product by Indigo Rose; I did not check
whether it is still sold or if a trial exists. The original project file is not in the installer, so you would rebuild it:

1. `python sfxtool.py extract ka_mod_v1x1a_setup.exe work` gives you the parts:
   * Global Functions: `work\texts\001_…lua` (already contains the fixes if you extract the fixed installer instead)
   * screen scripts: the `On Preload`, `On Next`, `On Ctrl Message`, `On Progress`, `On Pre/Post Uninstall`,
     `On Shutdown`, `On Install Error` files, and the language strings (`IDS_…`, `MSG_…`) in `work\texts\`
   * pictures, icon and helper programs: `work\internal\`
   * installed files with destinations: `work\payload\` + `work\file_table.csv`
2. Create a new project with the same screens (Welcome to Setup, License Agreement, Verify Serial Number, Select
   Install Folder, Ready to Install, progress, Finished Install, and the uninstall screens), paste the scripts and
   strings, add the files with the destinations from `file_table.csv`, add the internal files as additional files.
   The theme path stored in the project ("Contact" theme of Setup Factory 9) shows which theme was used.
3. What you do **not** get: the exact screen layouts (control positions, fonts and so on), the per-file install
   conditions and the other binary settings. They are in `irsetup.dat` in binary form that I have not decoded.
   Expect to recreate them by hand. I did not try this route myself.

**C. Extend the toolkit.** The file format is documented in section 2 and in the comments of `sfxtool.py`. The missing
piece for full control is decoding the binary screen / file-table records of `irsetup.dat` (their first fields
are already read by `parse_file_table`: source path, name, folder, extension, location, size, destination).

---

## 7. Limits and what is untested

* **No fixed installer, nor the toolkit's rebuilds, was ever run on Windows by me.** What I verified: the new file
  parses, every CRC is correct, the project data unpacks to exactly the intended text, the payload still decodes
  completely (1,751 files, sizes match the table), and `apply_ka_fixes.py` rebuilds the delivered installer bit for bit.
  What I could not verify: the behaviour of the scripts at run time (for example that `curl.exe` or PowerShell
  fetch your version file in your environment, and the free-space code on a drive with more than 2 TB).
  The Lua code was checked for balanced blocks only; there was no Lua interpreter available.
* Test every build in a virtual machine or Windows Sandbox. Keep the original installer.
* The patched installer is unsigned (the original was too), so Windows SmartScreen or antivirus software may warn.
* `--resize` is untested at run time (see section 4).
* A Setup Factory installer can only be rebuilt like this if the project data keeps its structure. If you edit
  with a hex editor instead of the tools, fix the CRC32 and the size fields yourself.
