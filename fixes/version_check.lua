-- Check to see what the latest version of KA is.
-- This check is optional: if the version file cannot be fetched by any method (offline, server down, etc.),
-- the installer silently continues with the installation.
function KA_ReadVersionFile(strFile)
	if not File.DoesExist(strFile) then
		return "";
	end
	local strText = TextFile.ReadToString(strFile);
	if (strText == nil) or (strText == "") then
		return "";
	end
	-- Ignore anything that does not look like a short version string (e.g. an HTML error or redirect page)
	if (String.Length(strText) > 200) or (String.Find(strText, "<", 1, false) ~= -1) then
		return "";
	end
	return strText;
end

strKAURL = "https://tron.hirnschwund.net/patches/latestversion.txt";
strKAFile = SessionVar.Expand("%TempFolder%\\ka.txt");
strKALogFile = SessionVar.Expand("%TempFolder%\\ka_versioncheck.log");
strKAValue = "";
strKALog = "Version check: "..strKAURL.."\r\n";

-- Method 1: the installer's built-in downloader (HTTPS needs port 443)
File.Delete(strKAFile, false, false, false, nil);
HTTP.Download(strKAURL, strKAFile, MODE_TEXT, 10, 443, nil, nil, nil);
kaErr = Application.GetLastError();
strKALog = strKALog.."1) HTTP.Download: error code "..tostring(kaErr).."\r\n";
if kaErr == 0 then
	strKAValue = KA_ReadVersionFile(strKAFile);
end

-- Method 2: curl.exe, which ships with Windows 10 (1803) and newer and supports modern TLS
strKACurl = SessionVar.Expand("%SystemFolder%\\curl.exe");
if (strKAValue == "") and File.DoesExist(strKACurl) then
	File.Delete(strKAFile, false, false, false, nil);
	kaRet = File.Run(strKACurl, "-s -f -L --ssl-no-revoke --max-time 10 -o \""..strKAFile.."\" \""..strKAURL.."\"", "", SW_HIDE, true);
	strKALog = strKALog.."2) curl.exe: return code "..tostring(kaRet)..", last error "..tostring(Application.GetLastError()).."\r\n";
	strKAValue = KA_ReadVersionFile(strKAFile);
end

-- Method 3: Windows PowerShell with TLS 1.2 forced
strKAPS = SessionVar.Expand("%SystemFolder%\\WindowsPowerShell\\v1.0\\powershell.exe");
if (strKAValue == "") and File.DoesExist(strKAPS) then
	File.Delete(strKAFile, false, false, false, nil);
	kaRet = File.Run(strKAPS, "-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \"[Net.ServicePointManager]::SecurityProtocol = 3072; $r = [Net.WebRequest]::Create('"..strKAURL.."'); $r.Timeout = 8000; $sr = New-Object IO.StreamReader($r.GetResponse().GetResponseStream()); [IO.File]::WriteAllText('"..strKAFile.."', $sr.ReadToEnd()); $sr.Close()\"", "", SW_HIDE, true);
	strKALog = strKALog.."3) PowerShell: return code "..tostring(kaRet)..", last error "..tostring(Application.GetLastError()).."\r\n";
	strKAValue = KA_ReadVersionFile(strKAFile);
end

strKALog = strKALog.."Result: ["..strKAValue.."]\r\n";
TextFile.WriteFromString(strKALogFile, strKALog, false);

-- Only if a version could be fetched, and it does not mention this version, offer the newer version
if strKAValue ~= "" then
	kaVersion = String.Find(strKAValue, "1.1a", 1, false);
	if kaVersion == -1 then
		Dialog.Message("Newer version available", "A newer version of the Killer App Mod is available ("..String.TrimLeft(String.TrimRight(strKAValue, nil), nil)..").\r\n\r\nYou will now be sent to a site online where\r\nyou can download the latest version.", MB_OK, MB_ICONINFORMATION);
		File.OpenURL("https://tron.hirnschwund.net/patches/", SW_MAXIMIZE);
		Application.Exit(0);
	end
end

