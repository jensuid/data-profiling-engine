; M7-T3 Windows installer — NSIS (SECURITY §6.4 installer, §6.5 uninstall/data policy).
;
; Consumes the PyInstaller onefile exe produced by data_profiling_engine.spec on a
; native Windows box (dist\DataProfilerEngine.exe) and builds an NSIS installer.
; The Windows track runs only on native Windows (BLUEPRINT Q4: physical/virtual,
; no container substitute) — this file is the repo-side template the operator
; compiles with `makensis`.
;
; Build (from repo root, in the Windows operator env — see docs runbook):
;     makensis /DPRODUCT_VERSION=0.1.0 packaging\windows\profiler.nsi
;
; Overridable via /D on the command line (keeps org facts out of the file):
;     /DPRODUCT_VERSION   installer + ARP display version  (default 0.1.0)
;     /DPUBLISHER          vendor shown in Add/Remove Programs (default "Data Profiling Engine")
;     /DICON_PATH          .ico for the exe/ARP (default none -> NSIS stock)
;
; Data-location contract (SECURITY §2 / §6.4): the app writes user data to
; %APPDATA% (storage.paths default), NOT the install dir. The installer therefore
; only ever touches $INSTDIR; the uninstaller must NOT delete user data
; (see Section "uninstall" below), per §6.5 "uninstall does not silently delete
; user data".

!include "MUI2.nsh"
!include "x64.nsh"

Unicode True

; ---- Identity (env-overridable; not hardcoded org facts) --------------------
!ifndef PUBLISHER
  !define PUBLISHER "Data Profiling Engine"
!endif
!ifndef PRODUCT_VERSION
  !define PRODUCT_VERSION "0.1.0"
!endif
!define PRODUCT_NAME "Data Profiling Engine"
!define EXE_NAME "DataProfilerEngine.exe"
; NSIS resolves relative File/OutFile paths against the SCRIPT dir, not the CWD.
; Default to the repo-root dist produced by PyInstaller; override with /DEXE_PATH.
!ifndef EXE_PATH
  !define EXE_PATH "..\..\dist\${EXE_NAME}"
!endif
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"

!ifndef OUT_FILE
  !define OUT_FILE "..\..\DataProfilerEngine-${PRODUCT_VERSION}-win64.exe"
!endif

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "${OUT_FILE}"
; Prefer a 64-bit install; per-user into LOCALAPPDATA keeps all-users vs per-user
; clean without requiring elevation (SECURITY §6.4).
InstallDir "$LOCALAPPDATA\Programs\${PRODUCT_NAME}"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
RequestExecutionLevel user
XPStyle on

!ifdef ICON_PATH
  Icon "${ICON_PATH}"
!endif

; ---- Modern UI --------------------------------------------------------------
!define MUI_ABORTWARNING
!define MUI_ICON "${NSISDIR}\Contrib\Graphics\Icons\modern-install.ico"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

; ---- Add/Remove Programs metadata ------------------------------------------
!define MUI_PRODUCT "${PRODUCT_NAME}"
!define MUI_VERSION "${PRODUCT_VERSION}"

Section "Application" SecApp
  SetOutPath "$INSTDIR"
  File "${EXE_PATH}"

  ; Desktop + Start-menu shortcuts (user scope).
  CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk" "$INSTDIR\${EXE_NAME}"
  CreateShortCut "$DESKTOP\${PRODUCT_NAME}.lnk" "$INSTDIR\${EXE_NAME}"

  ; Registry for ARP + uninstaller entry.
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "${PUBLISHER}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${EXE_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

; ---- WebView2 runtime prerequisite (SECURITY §6.4) --------------------------
; main.py --smoke already probes the registry and prints a graceful diagnostic
; when the Evergreen runtime is absent. Here we detect it at install time and
; warn the operator so a blank-window failure is not mistaken for an app crash.
Function .onInstSuccess
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${If} $0 == ""
    ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${EndIf}
  ${If} $0 == ""
    MessageBox MB_OK|MB_ICONEXCLAMATION \
      "WebView2 Runtime was not detected. Install the Evergreen runtime (or ship the bootstrapper) before launching, or the window may fail to render (SECURITY §6.4)."
  ${EndIf}
FunctionEnd

Section "Uninstall"
  ; Remove ONLY application files + shortcuts + our registry key.
  Delete "$INSTDIR\${EXE_NAME}"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$DESKTOP\${PRODUCT_NAME}.lnk"
  RMDir /r "$SMPROGRAMS\${PRODUCT_NAME}"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"

  ; SECURITY §6.5: never silently delete user data. Results/cache live under
  ; %APPDATA%; we intentionally leave them. If a "remove user data too" opt-in
  ; is wanted, add it as an explicit MUI page — do NOT rm -rf $APPDATA here.
SectionEnd
