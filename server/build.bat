@echo off
rem Builds sharepack-server.jar (needs JDK 8+). The stubs are compile-only and are not packed.
cd /d "%~dp0"
if exist out rmdir /s /q out
mkdir out\stubs out\classes
dir /s /b stubs\*.java > out\stubs.txt
javac --release 8 -encoding UTF-8 -d out\stubs @out\stubs.txt || exit /b 1
dir /s /b src\*.java > out\src.txt
javac --release 8 -encoding UTF-8 -cp out\stubs -d out\classes @out\src.txt || exit /b 1
xcopy /e /q /y resources\META-INF out\classes\META-INF\ >nul
copy /y resources\fabric.mod.json out\classes\ >nul
copy /y resources\plugin.yml out\classes\ >nul
copy /y resources\pack.mcmeta out\classes\ >nul
jar --create --file sharepack-server.jar --manifest resources\MANIFEST.MF -C out\classes . || exit /b 1
rmdir /s /q out
echo Done: sharepack-server.jar
