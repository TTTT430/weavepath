!macro customInstall
  CreateShortCut "$DESKTOP\WeavePath.lnk" "$INSTDIR\WeavePath.exe" "" "$INSTDIR\resources\weavepath-mark-v2.ico" 0
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
!macroend
