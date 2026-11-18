; Texts of installer.iss and update.iss (#include after [Languages]). English only exists in
; public builds: friends' builds (Friends defined) are Chinese only.

[CustomMessages]
chs.DesktopIcon=创建桌面快捷方式
chs.Shortcuts=快捷方式：
chs.Launch=启动随堂字幕
chs.DeleteSettings=是否同时删除设置、API key 和日志（%1）？%n录音文件（文档\Mitschrift Live，以及旧版的 文档\Live Capture）不会被删除。
chs.NotInstalled=没有找到已安装的随堂字幕（旧名 Live Capture）。%n请先用完整安装包（MitschriftLive-...-Setup.exe）安装，再运行这个更新。
#ifndef Friends
en.DesktopIcon=Create a desktop shortcut
en.Shortcuts=Shortcuts:
en.Launch=Start Mitschrift Live
en.DeleteSettings=Also delete the settings, API keys and logs (%1)?%nRecordings (Documents\Mitschrift Live) are kept.
en.NotInstalled=Mitschrift Live (formerly Live Capture) is not installed.%nInstall it with the full installer (MitschriftLive-...-Setup.exe) first, then run this update.
#endif
